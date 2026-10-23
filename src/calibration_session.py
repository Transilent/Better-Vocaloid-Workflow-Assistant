"""One multitrack trial, stable-WAV collection and automatic local SVP fitting."""


def canonical_kind(value):
    return 'vwa.' + value[5:] if isinstance(value, str) and value.startswith('bvwa.') else value


import copy
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid
from common import atomic_json,file_hash,native_path
from expression_calibration import CONTROLS,create_probe_set,score_identity

COMMIT_SCHEMA=1


def project_data(path):
    return json.loads(Path(native_path(path)).read_bytes().removesuffix(b'\x00').decode('utf-8-sig'))


def create_session(project,reference,destination,*,voice='lead',track_index=0,strength=.6,render_folder=None):
    import soundfile as sf
    from vocal_expression import TempoMap
    from pitch_accuracy import audit_svp_pitch
    if type(strength) not in (int,float) or not math.isfinite(strength) or not 0<strength<=1:
        raise ValueError('自动校准强度应大于 0，且不超过 100%。')
    project,reference,destination=Path(project).resolve(),Path(reference).resolve(),Path(destination).resolve()
    data=project_data(project)
    tracks=[i for i,t in enumerate(data.get('tracks',[])) if not t.get('mainRef',{}).get('isInstrumental')]
    if type(track_index) is not int or not 0<=track_index<len(tracks):raise ValueError('请选择存在的演唱轨。')
    original_index=tracks[track_index];track=data['tracks'][original_index]
    clock=TempoMap(data['time']['tempo']);offset=track.get('mainRef',{}).get('blickOffset',0)
    end=max((clock.to_seconds(n['onset']+n['duration']+offset) for n in track.get('mainGroup',{}).get('notes',[])),default=0)
    info=sf.info(str(reference))
    if end<=0 or info.samplerate<8000 or info.channels not in (1,2) or info.duration<end-.1:
        raise ValueError('参考人声必须从工程零秒开始，并覆盖所选声部的全部音符。')
    source_hash,reference_hash=file_hash(project),file_hash(reference)
    profile=create_probe_set(project,destination,voice,track_index)
    token=uuid.uuid4().hex[:12]
    variants=[];outputs={}
    for index,name in enumerate(('neutral',*CONTROLS)):
        single=destination/(voice+'-'+name+'.svp');variant=project_data(single)
        if index==0:batch=copy.deepcopy(variant)
        part=variant['tracks'][0];identity=str(uuid.uuid4())
        part['mainGroup']['uuid']=identity;part['mainRef']['groupID']=identity
        part['name']='VWA_'+token+'_'+voice+'_'+name;part['dispOrder']=index
        variants.append(part);outputs[name]=part['name']
    batch['tracks']=variants;batch['library']=[]
    render_folder=Path(render_folder).resolve() if render_folder else destination/'renders'
    render_folder.mkdir(parents=True,exist_ok=True)
    batch['renderConfig']={'destination':str(render_folder),'filename':'calibration','sampleRate':44100,
        'bitDepth':24,'numChannels':1,'aspirationFormat':'noAspiration','exportMixDown':False,
        'exportPitch':False,'bypassGain':True,'bypassPan':True,'bypassEffects':True}
    atomic_json(destination/'calibration.svp',batch)
    session={'schema':1,'project':str(project),'project_sha256':source_hash,
        'reference':str(reference),'reference_sha256':reference_hash,'voice':voice,'track_index':original_index,
        'score_sha256':score_identity(track,data),'strength':float(strength),'minimum_duration':end,
        'outputs':outputs,'state':'waiting','render_folder':str(render_folder),'pitch_diagnostics':audit_svp_pitch(track)}
    if source_hash!=file_hash(project) or reference_hash!=file_hash(reference):raise ValueError('准备校准时输入发生变化，请重新生成。')
    atomic_json(destination/'session.json',session)
    for name in ('neutral',*CONTROLS):(destination/(voice+'-'+name+'.svp')).unlink()
    from task_contract import register_session
    register_session(destination/'session.json')
    return destination/'session.json'


def read_session(path):
    path=Path(path).resolve();spec=json.loads(Path(native_path(path)).read_text(encoding='utf-8-sig'))
    if (not isinstance(spec,dict) or spec.get('schema')!=1 or spec.get('voice') not in ('lead','backing','vocals')
            or not isinstance(spec.get('outputs'),dict) or set(spec['outputs'])!={'neutral',*CONTROLS}
            or any(not isinstance(v,str) or not v.startswith(('VWA_', 'BVWA_')) or any(c in v for c in '/\\:') for v in spec['outputs'].values())
            or len(set(spec['outputs'].values()))!=7 or type(spec.get('track_index')) is not int
            or type(spec.get('minimum_duration')) not in (float,int) or not math.isfinite(spec['minimum_duration'])
            or spec['minimum_duration']<=0 or type(spec.get('strength')) not in (float,int)
            or not math.isfinite(spec['strength']) or not 0<spec['strength']<=1):
        raise ValueError('自动校准会话无效。')
    for key in ('project','reference','project_sha256','reference_sha256','score_sha256','render_folder'):
        if not isinstance(spec.get(key),str) or not spec[key]:raise ValueError('自动校准会话缺少输入记录。')
    if spec.get('render_channel_mode','standard') not in ('standard','sv-extra','sv-isolated'):
        raise ValueError('校准会话中的气声输出方式无效。')
    return spec


def unchanged_inputs(spec):
    for key in ('project','reference'):
        if file_hash(spec[key])!=spec[key+'_sha256']:
            raise ValueError(('原工程' if key=='project' else '参考人声')+'已改变，请重新准备校准。')


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


def _object(path):
    try:
        value=json.loads(Path(native_path(path)).read_text(encoding='utf-8-sig'))
        return value if isinstance(value,dict) else None
    except (OSError,ValueError,TypeError):
        return None


def _observer_error(kind,error):
    return {'kind':kind,'error_type':type(error).__name__,'error':str(error)}


def _fit_binding(session,spec,receipt,profile,profile_hash,output_name,strength):
    renders={}
    for name,row in sorted(receipt['renders'].items()):
        renders[name]={'path':str(Path(row['path']).resolve()),'sha256':row['sha256'],
            'frames':row.get('frames'),'sample_rate':row.get('sample_rate'),'channels':row.get('channels')}
    return _digest({'session':str(Path(session).resolve()),'project':str(Path(spec['project']).resolve()),
        'reference':str(Path(spec['reference']).resolve()),'project_sha256':spec['project_sha256'],
        'reference_sha256':spec['reference_sha256'],'score_sha256':spec['score_sha256'],
        'track_index':spec['track_index'],'voice':spec['voice'],'minimum_duration':spec['minimum_duration'],
        'profile':str(Path(profile).resolve()),'profile_sha256':profile_hash,
        'render_channel_mode':receipt.get('render_channel_mode',spec.get('render_channel_mode','standard')),
        'renders':renders,'output_name':output_name,'strength':float(strength)})


def _valid_fit_output_name(output_name):
    return (isinstance(output_name,str) and bool(output_name) and Path(output_name).name==output_name
            and '/' not in output_name and '\\' not in output_name and ':' not in output_name
            and output_name.lower().endswith('.svp'))


def _fit_report_path(root,output_name):
    return Path(root)/('fit-report.json' if output_name=='calibrated.svp' else Path(output_name).stem+'.fit-report.json')


def _fit_intent_path(root,output_name):
    token=hashlib.sha256(output_name.encode('utf-8')).hexdigest()[:24]
    return Path(root)/'commits'/('fit-'+token+'.json')


def _fit_intent(path,session,binding,output_name,output,report_path):
    value=_object(path)
    if not isinstance(value,dict):
        return None
    try:
        report=value.get('report')
        if (value.get('schema')!=COMMIT_SCHEMA or canonical_kind(value.get('kind'))!='vwa.calibration-commit'
                or value.get('session')!=str(Path(session).resolve()) or value.get('binding')!=binding
                or value.get('output_name')!=output_name or value.get('output')!=output.name
                or value.get('report_path')!=report_path.name
                or value.get('output_sha256')!=file_hash(output) or value.get('output_bytes')!=output.stat().st_size
                or value.get('content_sha256')!=_digest(project_data(output))
                or not isinstance(report,dict) or report.get('output')!=str(output)
                or report.get('sha256')!=value.get('output_sha256')
                or report.get('output_bytes')!=value.get('output_bytes')):
            return None
        return value
    except (OSError,ValueError,TypeError,KeyError):
        return None


def recoverable_fit_output(session,strength=None,channel_mode=None):
    """Return an exact committed basename whose report can still be repaired.

    This is a read-only query for the GUI. It accepts only the current session's
    input, render receipt, profile, score, strength and channel mode, and only a
    hash-bound output whose report is absent. It never runs fitting or updates
    state. Unknown, edited, stale or ambiguous files return None.
    """
    try:
        session=Path(session).resolve();root=session.parent;spec=read_session(session)
        strength=spec['strength'] if strength is None else strength
        if type(strength) not in (int,float) or not math.isfinite(strength) or not 0<=strength<=1:return None
        unchanged_inputs(spec)
        receipt=json.loads((root/'render-receipt.json').read_text(encoding='utf-8'))
        renders=receipt.get('renders')
        if not isinstance(renders,dict):return None
        for row in renders.values():
            if not isinstance(row,dict) or file_hash(row['path'])!=row['sha256']:return None
        original=project_data(spec['project']);index=spec['track_index']
        if not 0<=index<len(original['tracks']) or score_identity(original['tracks'][index],original)!=spec['score_sha256']:
            return None
        profile=root/'calibration.json';profile_hash=file_hash(profile)
        mode=channel_mode or receipt.get('render_channel_mode',spec.get('render_channel_mode','standard'))
        if mode not in ('standard','sv-extra','sv-isolated'):return None
        bound_receipt=copy.deepcopy(receipt);bound_receipt['render_channel_mode']=mode
        candidates=[];seen=set()
        commits=root/'commits'
        if commits.is_dir():
            for path in commits.glob('fit-*.json'):
                value=_object(path);name=value.get('output_name') if isinstance(value,dict) else None
                if _valid_fit_output_name(name) and name not in seen:
                    candidates.append((path.stat().st_mtime_ns,name));seen.add(name)
        if 'calibrated.svp' not in seen:candidates.append((-1,'calibrated.svp'))
        for _,output_name in sorted(candidates,reverse=True):
            output=root/output_name;report_path=_fit_report_path(root,output_name)
            if not output.is_file() or output.is_symlink() or report_path.exists():continue
            binding=_fit_binding(session,spec,bound_receipt,profile,profile_hash,output_name,strength)
            intent_path=_fit_intent_path(root,output_name)
            if intent_path.is_symlink():continue
            if _fit_intent(intent_path,session,binding,output_name,output,report_path) is not None:return output_name
        return None
    except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError):return None


def _write_fit_report(path,report,*,recovered=False):
    candidate=copy.deepcopy(report)
    candidate.update(commit_state='reported',report_verified=True,report_pending=False,
                     recovered_report=bool(recovered),report_path=str(path))
    if path.exists():
        saved=_object(path)
        if saved==candidate:
            return candidate
        candidate.update(commit_state='committed_unreported',report_verified=False,report_pending=True)
        candidate['report_preserved']=True
        candidate['report_error']=_observer_error('report',FileExistsError('校准报告路径出现了其他文件，已保留。'))
        return candidate
    error=None
    try:atomic_json(path,candidate)
    except Exception as failure:error=failure
    saved=_object(path)
    if saved!=candidate:
        candidate.update(commit_state='committed_unreported',report_verified=False,report_pending=True)
        candidate['report_error']=_observer_error('report',error or RuntimeError('报告写入后校验失败'))
    return candidate


def matches_task(profile,job):
    """Only opt the current task into a calibration of that task's own score/audio."""
    if not job:return False
    from expression_calibration import read_profile
    from project_exports import voices_for
    try:
        profile,job=Path(profile),Path(job)
        spec=read_profile(profile);session=read_session(profile.parent/'session.json')
        request=json.loads((job/'request.json').read_text(encoding='utf-8'))
        data=project_data(job/'exports/voices.svp')
        tracks=[t for t in data['tracks'] if not t.get('mainRef',{}).get('isInstrumental')]
        voices=voices_for(request)
        for voice,row in spec['tracks'].items():
            if (voice not in voices or voices.index(voice)>=len(tracks)
                    or score_identity(tracks[voices.index(voice)],data)!=row['score_sha256']
                    or file_hash(job/'audio'/(voice+'.wav'))!=session['reference_sha256']):return False
        return True
    except (OSError,ValueError,TypeError,KeyError):return False


def writer_closed(path):
    """On Windows, reject a WAV still held by a renderer, even at stable size."""
    if os.name!='nt':return True
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,
                                wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    handle=kernel.CreateFileW(native_path(Path(path).resolve()),0x80000000,0,None,3,0x80,None)
    if handle in (None,ctypes.c_void_p(-1).value):return False
    kernel.CloseHandle(handle);return True


class RenderCollector:
    def __init__(self,session,folder=None,*,stable_seconds=2.,clock=time.monotonic,channel_mode=None):
        if not math.isfinite(stable_seconds) or stable_seconds<0:raise ValueError('文件稳定时间无效。')
        self.session=Path(session).resolve();self.spec=read_session(session)
        self.folder=Path(folder or self.spec['render_folder']).resolve()
        self.channel_mode=channel_mode or self.spec.get('render_channel_mode','standard')
        if self.channel_mode not in ('standard','sv-extra','sv-isolated'):raise ValueError('请选择正确的 SV 气声输出方式。')
        self.clock,self.stable_seconds=clock,stable_seconds;self.seen={}

    def scan(self):
        now=self.clock();found={};waiting=[]
        files=list(self.folder.glob('*.wav')) if self.folder.is_dir() else []
        for control,token in self.spec['outputs'].items():
            candidates=[p for p in files if p.stem.casefold().endswith(token.casefold()) and p.is_file()]
            if len(candidates)>1:raise ValueError('同一试音有多份文件，请移走重复文件：'+token)
            if not candidates:waiting.append(control);self.seen.pop(control,None);continue
            path=candidates[0];stat=path.stat();stamp=(str(path),stat.st_size,stat.st_mtime_ns)
            old=self.seen.get(control)
            if old is None or old[0]!=stamp:self.seen[control]=(stamp,now)
            if stat.st_size<=44 or now-self.seen[control][1]<self.stable_seconds or not writer_closed(path):
                waiting.append(control);continue
            found[control]=path
        return found,waiting

    def collect(self,cancel=lambda:None):
        import numpy as np
        import soundfile as sf
        cancel();unchanged_inputs(self.spec)
        found,waiting=self.scan()
        if waiting:return None,waiting
        measured={};shape=None
        for control,path in found.items():
            cancel();before=path.stat();digest=file_hash(path)
            with sf.SoundFile(str(path)) as stream:
                current=(stream.frames,stream.samplerate,stream.channels)
                allowed=(1,2) if self.channel_mode=='standard' else (2,4)
                if stream.channels not in allowed:
                    raise ValueError(f'试音有 {stream.channels} 个声道，当前气声输出选项不匹配：{path.name}。'
                                     '请在助手中选择与 SV 一致的追加／隔离气声方式；普通导出请选择气声“无”。')
                if not 8000<=stream.samplerate<=192000:
                    raise ValueError(f'试音采样率 {stream.samplerate} Hz 不受支持：{path.name}。请使用 44.1 或 48 kHz。')
                duration=stream.frames/stream.samplerate
                if duration<self.spec['minimum_duration']-.1:
                    raise ValueError(f'试音过短：{duration:.2f} 秒，需覆盖到 {self.spec["minimum_duration"]:.2f} 秒：{path.name}。'
                                     '请从零秒导出全部演唱区间。')
                if shape is not None and current!=shape:raise ValueError('七份试音的长度、采样率和声道必须一致。')
                shape=current;read=0
                for block in stream.blocks(blocksize=65536,dtype='float32',always_2d=True):
                    cancel();read+=len(block)
                    if not np.isfinite(block).all():raise ValueError('试音含有无效采样：'+path.name)
                if read!=stream.frames:raise ValueError('试音文件尚未完整写入：'+path.name)
            after=path.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or digest!=file_hash(path):
                self.seen.pop(control,None);return None,[control]
            measured[control]={'path':str(path),'sha256':digest,'frames':shape[0],'sample_rate':shape[1],'channels':shape[2]}
        spec=json.loads((self.session.parent/'calibration.json').read_text(encoding='utf-8'))
        row=spec['tracks'][self.spec['voice']]
        row['render_channel_mode']=self.channel_mode
        row['baseline']=measured['neutral']['path']
        for probe in row['probes']:probe['audio']=measured[probe['parameter']]['path']
        cancel();atomic_json(self.session.parent/'calibration.json',spec)
        atomic_json(self.session.parent/'render-receipt.json',{'schema':1,'renders':measured,'render_channel_mode':self.channel_mode})
        self.spec['render_channel_mode']=self.channel_mode;self.spec['render_folder']=str(self.folder)
        atomic_json(self.session,self.spec)
        return measured,[]


def fit_session(session,*,cancel=lambda:None,progress=lambda text:None,output_name='calibrated.svp',strength=None):
    from tuning_engine import process_project,primary_report
    session=Path(session).resolve();root=session.parent;spec=read_session(session)
    if not _valid_fit_output_name(output_name):
        raise ValueError('校准输出必须是会话目录中的 SVP 文件名。')
    strength=spec['strength'] if strength is None else strength
    if type(strength) not in (int,float) or not math.isfinite(strength) or not 0<=strength<=1:
        raise ValueError('校准拟合强度无效。')
    cancel();unchanged_inputs(spec)
    receipt=json.loads((root/'render-receipt.json').read_text(encoding='utf-8'))
    for row in receipt['renders'].values():
        cancel()
        if file_hash(row['path'])!=row['sha256']:raise ValueError('试音已改变，请重新收集后拟合。')
    original=project_data(spec['project']);index=spec['track_index']
    if not 0<=index<len(original['tracks']) or score_identity(original['tracks'][index],original)!=spec['score_sha256']:
        raise ValueError('校准工程的所选声部已改变。')
    output=root/output_name
    report_path=_fit_report_path(root,output_name)
    profile=root/'calibration.json';profile_hash=file_hash(profile)
    binding=_fit_binding(session,spec,receipt,profile,profile_hash,output_name,strength)
    intent_path=_fit_intent_path(root,output_name)
    if output.exists() or report_path.exists():
        if not output.is_file() or report_path.exists():
            raise FileExistsError('校准结果已存在，请使用新的文件名，避免覆盖人工修改。')
        prepared=_fit_intent(intent_path,session,binding,output_name,output,report_path)
        if prepared is None:
            raise FileExistsError('校准结果已存在，但没有与本输入、试音和强度匹配的提交意图；已保留，请使用新文件名。')
        return _write_fit_report(report_path,prepared['report'],recovered=True)
    pending=root/'fit.pending.svp'
    try:
        progress('正在分析试音并拟合参数…')
        original,transaction=process_project(original,{'expression_mode':'calibrated','track_indices':[index],
            'reference_sources':[{'track_index':index,'voice':spec['voice'],'path':spec['reference']}],
            'reference_calibration':str(profile),'dynamics_strength':strength},
            cancel=cancel,cache=root/'features',progress=progress)
        result=primary_report(original,transaction)
        # The new file lives beside the session, so preserve original media by
        # resolving its existing relative references before moving the project.
        for track in original['tracks']:
            for ref in [track.get('mainRef',{}),*track.get('groups',[])]:
                audio=ref.get('audio')
                if isinstance(audio,dict) and audio.get('filename') and not Path(audio['filename']).is_absolute():
                    audio['filename']=str((Path(spec['project']).parent/audio['filename']).resolve())
        cancel();unchanged_inputs(spec)
        if file_hash(profile)!=profile_hash:raise ValueError('拟合期间校准配置发生变化，请重试。')
        for row in receipt['renders'].values():
            cancel()
            if file_hash(row['path'])!=row['sha256']:raise ValueError('拟合期间试音发生变化，请重新收集。')
        from calibration_workflow import neutral_project
        neutral=neutral_project(session,original)
        from pitch_accuracy import audit_svp_pitch
        diagnostics=audit_svp_pitch(original['tracks'][index])
        atomic_json(pending,original)
        if not pending.is_file() or project_data(pending)!=original:
            raise ValueError('校准临时工程写入校验失败。')
        output_hash=file_hash(pending);output_bytes=pending.stat().st_size
        report={'schema':1,'output':str(output),'output_bytes':output_bytes,'sha256':output_hash,'neutral':str(neutral),
            'calibration_fit':result,'tuning_transaction':transaction,'project_commit_count':1,
            'pitch_diagnostics':diagnostics,'profile':str(profile),'reference_sha256':spec['reference_sha256'],
            'commit_state':'committed','report_verified':False,'report_pending':True,
            'commit_intent':intent_path.relative_to(root).as_posix()}
        intent={'schema':COMMIT_SCHEMA,'kind':'vwa.calibration-commit','session':str(session),
            'binding':binding,'output_name':output_name,'output':output.name,'report_path':report_path.name,
            'output_sha256':output_hash,'output_bytes':output_bytes,'content_sha256':_digest(original),
            'report':copy.deepcopy(report)}
        atomic_json(intent_path,intent)
        # Recheck cancellation and inputs while the transaction is still abortable.
        cancel();unchanged_inputs(spec)
        if file_hash(profile)!=profile_hash:raise ValueError('校准提交前配置发生变化，未提交，请重试。')
        for row in receipt['renders'].values():
            cancel()
            if file_hash(row['path'])!=row['sha256']:raise ValueError('校准提交前试音发生变化，未提交，请重新收集。')
        if file_hash(pending)!=output_hash:raise ValueError('校准临时工程在提交前发生变化，未覆盖；请重试。')
        if output.exists() or report_path.exists():
            raise FileExistsError('校准提交前出现了其他结果文件，未覆盖；请检查后重试。')
        pending.rename(output);pending=None
        if (not output.is_file() or file_hash(output)!=output_hash or output.stat().st_size!=output_bytes
                or project_data(output)!=original):
            raise OSError('校准工程发布后哈希校验失败。')
        if result['voicebank_calibrated']:
            from calibration_workflow import register_expression
            reference=Path(spec['reference']).resolve();job=reference.parent.parent
            if reference==job/'audio'/(spec['voice']+'.wav'):
                ordinal=[i for i,t in enumerate(original['tracks']) if not t.get('mainRef',{}).get('isInstrumental')].index(index)
                try:report['expression_registered']=register_expression(job,output,spec['voice'],ordinal)
                except Exception as error:report['expression_registration_error']=str(error)
        # Persist final report evidence before report publication. Failure to
        # update this metadata copy cannot undo the committed project.
        try:
            intent['report']=copy.deepcopy(report);intent['report_final']=True
            atomic_json(intent_path,intent)
        except Exception as error:report['intent_update_error']=str(error)
        return _write_fit_report(report_path,report)
    finally:
        if pending is not None:pending.unlink(missing_ok=True)


def wait_and_fit(session,folder=None,*,cancel=lambda:None,progress=lambda text:None,timeout=1800,interval=.5,channel_mode=None,
                 output_name='calibrated.svp',strength=None,on_state=lambda state:None):
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (timeout,interval)):
        raise ValueError('等待时间无效。')
    collector=RenderCollector(session,folder,channel_mode=channel_mode);started=time.monotonic();last=None;last_state=None
    while True:
        cancel();found,waiting=collector.scan()
        rows={name:'stable' if name in found else 'writing' if name in collector.seen else 'missing' for name in collector.spec['outputs']}
        if rows!=last_state:on_state({'phase':'waiting','files':rows,'done':len(found),'total':7});last_state=rows
        message='等待 SV 分轨导出：'+str(len(found))+' / 7 份文件已稳定。'
        if message!=last:progress(message);last=message
        if not waiting:
            receipt,waiting=collector.collect(cancel)
            if receipt:
                on_state({'phase':'fitting','files':{name:'validated' for name in receipt},'done':7,'total':7})
                return fit_session(session,cancel=cancel,progress=progress,output_name=output_name,strength=strength)
        if time.monotonic()-started>=timeout:raise TimeoutError('等待试音超时。已保留会话，可完成分轨导出后继续。')
        # Cancellation stays responsive even with a longer caller polling interval.
        until=time.monotonic()+interval
        while time.monotonic()<until:
            cancel();time.sleep(min(.1,max(0,until-time.monotonic())))
