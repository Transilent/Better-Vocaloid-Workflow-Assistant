"""User-facing calibration checks and safe preparation; no editor automation."""
import copy
import difflib
import json
import math
import re
from pathlib import Path
from common import atomic_json,file_hash


def _retained_note_values(note,label,index):
    if not isinstance(note,dict):
        raise ValueError(f'{label}音符[{index}]不是有效对象。')
    values=[]
    for key in ('onset','duration','pitch'):
        value=note.get(key)
        if type(value) not in (int,float) or not math.isfinite(value):
            raise ValueError(f'{label}音符[{index}]的 {key} 不是有限数值。')
        values.append(value)
    return tuple(values)


def _retained_note_label(note,label,index,values):
    onset,duration,pitch=values
    return f'{label}音符[{index}] onset={onset!r}, duration={duration!r}, pitch={pitch!r}'


def _strict_retained_note_matches(source_notes,target_notes):
    target_by_key={}
    target_values={}
    for index,note in enumerate(target_notes):
        values=_retained_note_values(note,'新版工程',index)
        target_values[index]=values
        target_by_key.setdefault(values,[]).append(index)
    rows=[]
    used_targets=set()
    for source_index,note in enumerate(source_notes):
        values=_retained_note_values(note,'来源工程',source_index)
        candidates=[index for index in target_by_key.get(values,[]) if index not in used_targets]
        if not candidates:
            raise ValueError('严格沿用参数时来源音符未匹配：'+
                _retained_note_label(note,'来源',source_index,values)+'。')
        if len(candidates)!=1:
            details='、'.join(_retained_note_label(target_notes[i],'新版',i,target_values[i]) for i in candidates)
            raise ValueError('严格沿用参数时音符匹配不唯一，无法保证一对一：'+
                _retained_note_label(note,'来源',source_index,values)+' -> '+details+'。')
        target_index=candidates[0]
        used_targets.add(target_index)
        rows.append({'source_index':source_index,'target_index':target_index,
            'source':dict(zip(('onset','duration','pitch'),values)),
            'target':dict(zip(('onset','duration','pitch'),target_values[target_index])),
            'time_delta_seconds':0.0,'pitch_delta_semitones':0.0,'duration_delta':0,
            'used_time_tolerance':False,'used_pitch_tolerance':False})
    extras=[index for index in range(len(target_notes)) if index not in used_targets]
    if extras:
        details='、'.join(_retained_note_label(target_notes[i],'新版',i,target_values[i]) for i in extras)
        raise ValueError('严格沿用参数时新版工程包含额外音符：'+details+
            '。为避免把绝对作者曲线用于新增音符，已停止沿用。')
    rows.sort(key=lambda row:row['source_index'])
    return rows


def _legacy_retained_note_matches(source_notes,target_notes,clock):
    time_tolerance=.12
    pitch_tolerance=1.
    source_values=[_retained_note_values(note,'来源工程',index)
        for index,note in enumerate(source_notes)]
    target_values=[_retained_note_values(note,'新版工程',index)
        for index,note in enumerate(target_notes)]
    candidates_by_source={};conflicts=[]
    for source_index,old_values in enumerate(source_values):
        old_onset,_,old_pitch=old_values
        candidates=[]
        for target_index,new_values in enumerate(target_values):
            new_onset,_,new_pitch=new_values
            time_delta=abs(clock.to_seconds(new_onset)-clock.to_seconds(old_onset))
            pitch_delta=abs(new_pitch-old_pitch)
            if time_delta<=time_tolerance and pitch_delta<=pitch_tolerance:
                candidate={'target_index':target_index,'time_delta_seconds':time_delta,
                    'pitch_delta_semitones':pitch_delta}
                candidates.append(candidate)
        candidates_by_source[source_index]=candidates
        if len(candidates)>1:
            conflicts.append({'source_index':source_index,'source':
                dict(zip(('onset','duration','pitch'),old_values)),'candidates':candidates})
    for candidates in candidates_by_source.values():
        candidates.sort(key=lambda row:(row['time_delta_seconds'],row['pitch_delta_semitones'],row['target_index']))
    assigned_targets={}
    def assign(source_index,seen):
        for candidate in candidates_by_source.get(source_index,[]):
            target_index=candidate['target_index']
            if target_index in seen:continue
            seen.add(target_index)
            other=assigned_targets.get(target_index)
            if other is None or assign(other,seen):
                assigned_targets[target_index]=source_index
                return True
        return False
    for source_index in sorted(candidates_by_source):
        assign(source_index,set())
    assigned_sources={}
    for target_index,source_index in assigned_targets.items():
        assigned_sources[source_index]=next(candidate for candidate in candidates_by_source[source_index]
            if candidate['target_index']==target_index)
    unmatched=[index for index in range(len(source_notes)) if index not in assigned_sources]
    if unmatched:
        details='、'.join(_retained_note_label(source_notes[i],'来源',i,source_values[i]) for i in unmatched)
        raise ValueError('legacy 沿用参数仍有来源音符未匹配：'+details+
            f'。仅在 {time_tolerance:.2f} 秒、{pitch_tolerance:g} 半音容差内做一对一匹配。')
    extras=[index for index in range(len(target_notes)) if index not in assigned_targets]
    if extras:
        details='、'.join(_retained_note_label(target_notes[i],'新版',i,target_values[i]) for i in extras)
        raise ValueError('legacy 沿用参数的新版工程包含额外音符：'+details+
            '。即使启用旧容差也不会把作者绝对曲线扩展到新增音符。')
    rows=[]
    for source_index,old_values in enumerate(source_values):
        target_index=assigned_sources[source_index]['target_index']
        new_values=target_values[target_index]
        candidate=assigned_sources[source_index]
        rows.append({'source_index':source_index,'target_index':target_index,
            'source':dict(zip(('onset','duration','pitch'),old_values)),
            'target':dict(zip(('onset','duration','pitch'),new_values)),
            'time_delta_seconds':candidate['time_delta_seconds'],
            'pitch_delta_semitones':candidate['pitch_delta_semitones'],
            'duration_delta':new_values[1]-old_values[1],
            'used_time_tolerance':candidate['time_delta_seconds']>0,
            'used_pitch_tolerance':candidate['pitch_delta_semitones']>0})
    return rows,conflicts


def _authored_retained_curves(parameters,controls):
    if not isinstance(parameters,dict):
        raise ValueError('来源工程的参数容器无效。')
    curves={}
    for name in controls:
        curve=parameters.get(name)
        if not curve:continue
        points=curve.get('points') if isinstance(curve,dict) else None
        if not points:continue
        if (not isinstance(points,list) or len(points)<2 or len(points)%2
                or any(type(value) not in (int,float) or not math.isfinite(value) for value in points)):
            raise ValueError('来源工程的参数曲线结构或有限数值无效：'+name)
        positions=points[::2]
        if any(left>=right for left,right in zip(positions,positions[1:])):
            raise ValueError('来源工程的参数曲线时间坐标未严格递增：'+name)
        # The solver's nominal range is not an SVP authoring constraint. Keep
        # the complete curve object exactly as authored, including mode/value
        # types and all points outside that range.
        curves[name]=copy.deepcopy(curve)
    return curves


def _retained_control_report(name,curve,controls):
    points=curve['points'];values=points[1::2];lo,hi,_,_=controls[name]
    outside=[{'point_index':index,'position':points[index*2],'value':points[index*2+1],
              'nominal_minimum':lo,'nominal_maximum':hi}
             for index,value in enumerate(values) if value<lo or value>hi]
    return {'points':len(values),'minimum':min(values),'maximum':max(values),
        'interpolation':curve.get('mode'),'provenance':'author_authored_preserved',
        'outside_nominal_range_count':len(outside),'outside_nominal_range':outside}


def retain_project(target_data, source_data, *, track_index=0, source_track_index=0,
                   match_policy='exact'):
    """Copy authored expression onto an unambiguously corresponding score revision.

    ``exact`` is the pure API used by the tuning engine: every source note must
    match one target note by onset, duration and pitch, and every target note
    must be claimed by exactly one source note. ``legacy`` is reserved for the
    old file wrapper and note-regeneration compatibility: it may use the
    historical 120 ms / one-semitone onset-pitch tolerance, but still requires
    complete one-to-one coverage and records every tolerance and candidate
    conflict. Both policies fail closed on unmatched or extra notes.
    """
    from expression_calibration import CONTROLS
    if match_policy not in ('exact','legacy'):
        raise ValueError('沿用参数匹配策略必须为 exact 或 legacy。')
    if not isinstance(target_data,dict) or not isinstance(source_data,dict):
        raise ValueError('保留参数需要两个有效的 SVP 工程。')
    output,prior=copy.deepcopy(target_data),copy.deepcopy(source_data)
    target_index,track=selected_track(output,track_index)
    source_index,old=selected_track(prior,source_track_index)
    if track.get('groups') or old.get('groups'):
        raise ValueError('保留参数目前需要单一主音符组。')
    ref,old_ref=track.get('mainRef',{}),old.get('mainRef',{})
    for label,main_ref in (('新版',ref),('原参数',old_ref)):
        if main_ref.get('blickAbsoluteBegin',0)!=0 or main_ref.get('blickAbsoluteEnd',-1)!=-1:
            raise ValueError(label+'工程使用了裁剪 mainRef（blickAbsoluteBegin='+
                repr(main_ref.get('blickAbsoluteBegin'))+'，blickAbsoluteEnd='+
                repr(main_ref.get('blickAbsoluteEnd'))+'）；沿用参数要求 source/target 都是未裁剪 mainGroup。')
    if output.get('time')!=prior.get('time') or ref.get('blickOffset',0)!=old_ref.get('blickOffset',0):
        raise ValueError('工程速度表或时间位置已经变化，无法直接沿用原参数。')
    new_group,old_group=track.get('mainGroup',{}),old.get('mainGroup',{})
    new_notes,old_notes=new_group.get('notes'),old_group.get('notes')
    if not isinstance(new_notes,list) or not isinstance(old_notes,list) or not old_notes:
        raise ValueError('新版或原参数工程缺少可核对的演唱音符。')
    if match_policy=='exact':
        match_rows=_strict_retained_note_matches(old_notes,new_notes);conflicts=[]
        time_tolerance=0.;pitch_tolerance=0.
    else:
        from vocal_expression import TempoMap
        match_rows,conflicts=_legacy_retained_note_matches(old_notes,new_notes,TempoMap(output['time']['tempo']))
        time_tolerance=.12;pitch_tolerance=1.
    curves=_authored_retained_curves(old_group.get('parameters',{}),CONTROLS)
    if not curves:raise ValueError('来源工程没有可保留的演唱参数，已停止导出。')
    control_report={name:_retained_control_report(name,curve,CONTROLS) for name,curve in curves.items()}
    outside_nominal={name:row['outside_nominal_range'] for name,row in control_report.items()
        if row['outside_nominal_range_count']}
    old_bank=old_ref.get('database',{})
    current_bank=ref.get('database',{})
    if current_bank.get('name') and current_bank.get('name')!=old_bank.get('name'):
        raise ValueError('新版工程与原参数工程的声库不同。')
    params=new_group.setdefault('parameters',{})
    params.update(curves)
    if old_bank.get('name'):
        track.setdefault('mainRef',{})['database']=copy.deepcopy(old_bank)
    # Preserve score/pitch controls; carry only expression offsets from the
    # uniquely matched source note and list every bias replaced during transfer.
    keys=['param'+name[0].upper()+name[1:] for name in CONTROLS]
    removed_voice,restored_voice=[],[]
    voice=track.setdefault('mainRef',{}).setdefault('voice',{})
    old_voice=old_ref.get('voice',{})
    for key in keys:
        if key in voice:
            voice.pop(key,None);removed_voice.append(key)
        if key in old_voice:
            voice[key]=copy.deepcopy(old_voice[key]);restored_voice.append(key)
    removed_notes,restored_notes=[],[]
    for row in match_rows:
        old_note,new_note=old_notes[row['source_index']],new_notes[row['target_index']]
        attributes=new_note.get('attributes',{})
        source_attributes=old_note.get('attributes',{})
        for key in keys:
            if key in attributes:
                attributes.pop(key,None);removed_notes.append({'note_onset':new_note['onset'],'key':key})
            if key in source_attributes:
                new_note.setdefault('attributes',{})[key]=copy.deepcopy(source_attributes[key])
                restored_notes.append({'note_onset':new_note['onset'],'key':key})
    report={
        'kind':'retained_automation','target_track_index':target_index,
        'source_track_index':source_index,'matched_source_notes':len(match_rows),
        'source_notes':len(old_notes),'target_notes':len(new_notes),'new_fit':False,
        'match_policy':match_policy,
        'matching':{'one_to_one':True,'complete':True,
            'time_tolerance_seconds':time_tolerance,'pitch_tolerance_semitones':pitch_tolerance,
            'duration_required':match_policy=='exact','unmatched_policy':'fail_closed',
            'extra_target_policy':'fail_closed',
            'legacy_tolerance_used':any(row['used_time_tolerance'] or row['used_pitch_tolerance']
                for row in match_rows),
            'absolute_curve_retiming':'explicit_legacy' if match_policy=='legacy' and any(
                row['time_delta_seconds'] for row in match_rows) else 'none',
            'matches':match_rows,'candidate_conflicts':conflicts},
        'controls':control_report,'curve_source':'author_authored_preserved',
        'nominal_ranges_are_solver_only':True,
        'author_authored_outside_nominal_range':outside_nominal,
        'bias_cleanup':{'track_voice_removed':removed_voice,
            'track_voice_restored':restored_voice,
            'note_offsets_removed':removed_notes,'note_offsets_restored':restored_notes,
            'removed_count':len(removed_voice)+len(removed_notes)}
    }
    return output,report


def retain_parameters(target, source, *, track_index=0, source_track_index=0,
                      match_policy='legacy'):
    """Keep an existing performance on a compatible score revision.

    This compatibility wrapper explicitly uses the one-to-one ``legacy``
    tolerance by default for old note-regeneration tasks. Callers that know the
    score has exact note identity can request ``match_policy='exact'``. The
    tuning engine's pure retained route calls :func:`retain_project` directly
    and therefore defaults to exact matching.
    """
    from calibration_session import project_data
    target,source=Path(target).resolve(),Path(source).resolve()
    if target==source:raise ValueError('请为保留参数的新版工程使用不同文件。')
    before={str(p):file_hash(p) for p in (target,source)}
    data,prior=project_data(target),project_data(source)
    output,report=retain_project(data,prior,track_index=track_index,
        source_track_index=source_track_index,match_policy=match_policy)
    if any(file_hash(Path(p))!=h for p,h in before.items()):
        raise ValueError('保留参数期间工程发生变化，请重试。')
    atomic_json(target,output)
    written=project_data(target)['tracks'][report['target_track_index']]['mainGroup']['parameters']
    expected=output['tracks'][report['target_track_index']]['mainGroup']['parameters']
    if any(written.get(name)!=value for name,value in expected.items() if name in report['controls']):
        raise ValueError('演唱参数写入后校验失败。')
    report['source_sha256']=before[str(source)]
    report['wrapper']={'name':'retain_parameters','match_policy':match_policy}
    return report


def register_expression(job, project, voice='lead', track_index=0):
    """Remember the actual parameter-bearing result for subsequent reprocessing."""
    from common import saved_path
    from calibration_session import project_data
    from expression_calibration import CONTROLS,score_identity
    job,project=Path(job).resolve(),Path(project).resolve()
    if not (job/'request.json').is_file() or not (job/'audio/source.wav').is_file():return False
    project_value=project_data(project)
    _,track=selected_track(project_value,track_index)
    if not any(track.get('mainGroup',{}).get('parameters',{}).get(k,{}).get('points') for k in CONTROLS):return False
    record=job/'expression-result.json'
    data=json.loads(record.read_text(encoding='utf-8')) if record.is_file() else {'schema':1,'tracks':{}}
    data['source_audio_sha256']=file_hash(job/'audio/source.wav')
    data['tracks'][voice]={'project':saved_path(project),'sha256':file_hash(project),'track_index':track_index,
        'score_sha256':score_identity(track,project_value),'database':track.get('mainRef',{}).get('database',{})}
    atomic_json(record,data)
    from task_contract import register
    register(job, [{'path':project,'role':'calibrated_project'}], 'calibration_result', parents=('audio','midi'))
    return True


def inherit_expression(previous,job,request):
    """Snapshot the previous verified result; do not point at mutable old files."""
    import shutil
    from common import app_path
    previous,job=Path(previous).resolve(),Path(job).resolve()
    record=previous/'expression-result.json'
    if 'svp' not in request.get('project_formats',[]) or request.get('reference_calibration') or not record.is_file():return
    data=json.loads(record.read_text(encoding='utf-8'))
    if data.get('schema')!=1 or data.get('source_audio_sha256')!=file_hash(previous/'audio/source.wav'):
        raise ValueError('原演唱参数与音乐来源不一致，已停止重新处理。')
    entries={}
    from project_exports import voices_for
    for voice,row in data['tracks'].items():
        if voice not in voices_for(request):continue
        source=app_path(row['project'])
        digest=file_hash(source)
        if digest!=row['sha256']:
            from calibration_session import project_data
            from expression_calibration import score_identity
            value=project_data(source);_,track=selected_track(value,row['track_index'])
            if (score_identity(track,value)!=row.get('score_sha256')
                    or track.get('mainRef',{}).get('database',{})!=row.get('database')):
                raise ValueError('原参数工程的乐谱或声库已改变，无法自动沿用。')
            # Editor saves and expression-only edits are safe to snapshot;
            # do not require an invisible manual re-registration step.
            row['sha256']=digest
        target=job/'input'/('retained-'+voice+'.svp');target.parent.mkdir(exist_ok=True)
        shutil.copy2(source,target)
        if file_hash(target)!=digest or file_hash(source)!=digest:raise ValueError('复制期间原参数工程发生变化，请重试。')
        entries[voice]={'file':target.relative_to(job).as_posix(),'sha256':file_hash(target),'track_index':row['track_index']}
    if entries:
        request['retained_expression']={'source_audio_sha256':data['source_audio_sha256'],'tracks':entries}
        atomic_json(job/'request.json',request)


def retained_signature(job,request):
    spec=request['retained_expression'];job=Path(job).resolve()
    if file_hash(job/'audio/source.wav')!=spec['source_audio_sha256']:
        raise ValueError('当前音乐与原参数的来源不同，无法保留参数。')
    for row in spec['tracks'].values():
        path=(job/row['file']).resolve()
        if not path.is_relative_to(job) or file_hash(path)!=row['sha256']:
            raise ValueError('待保留的参数工程缺失或已改变，已停止导出。')
    return {'spec':spec,'adapter':file_hash(Path(__file__))}


def apply_retained_expression(path,job,request):
    from project_exports import voices_for
    retained_signature(job,request)
    result={}
    for voice,row in request['retained_expression']['tracks'].items():
        result[voice]=retain_parameters(path,Path(job)/row['file'],
            track_index=voices_for(request).index(voice),source_track_index=row['track_index'])
    return result


def selected_track(data,index):
    tracks=[(i,t) for i,t in enumerate(data.get('tracks',[])) if not t.get('mainRef',{}).get('isInstrumental')]
    if type(index) is not int or not 0<=index<len(tracks):raise ValueError('请选择有效的演唱轨。')
    return tracks[index]


def lyric_review(data,index,reference):
    """Locate text differences; never invent note timings or new lyrics."""
    from common import clean_lyrics
    from vocal_expression import TempoMap
    _,track=selected_track(data,index);clock=TempoMap(data['time']['tempo'])
    wanted=clean_lyrics(reference);tokens=[];owners=[];offset=track.get('mainRef',{}).get('blickOffset',0)
    for note in track.get('mainGroup',{}).get('notes',[]):
        text=clean_lyrics(note.get('lyrics',''))
        if text in ('','-','+','_'):continue
        for char in text:tokens.append(char);owners.append(note)
    actual=''.join(tokens)
    if not wanted:return {'status':'no_reference','candidates':[]}
    # Comparing Chinese text with Japanese romaji or Chinese pinyin creates
    # false omissions. Restrict this first review to matching Hanzi scripts.
    han=lambda text:len(re.findall(r'[\u3400-\u9fff]',text))/max(1,len(text))
    if han(wanted)<.8 or han(actual)<.8:return {'status':'different_script','candidates':[]}
    candidates=[]
    for kind,a,b,c,d in difflib.SequenceMatcher(None,wanted,actual,autojunk=False).get_opcodes():
        if kind=='equal':continue
        before=owners[c-1] if c else None;after=owners[d] if d<len(owners) else None
        start=clock.to_seconds((before['onset']+before['duration'] if before else 0)+offset)
        end=clock.to_seconds(after['onset']+offset) if after else start
        candidates.append({'kind':kind,'expected':wanted[a:b],'actual':actual[c:d],
                           'start':round(start,3),'end':round(max(start,end),3)})
    return {'status':'review' if candidates else 'matched','reference_characters':len(wanted),
            'recognized_characters':len(actual),'candidates':candidates}


def inspect_inputs(project,reference,*,track_index=0,job=None,voice='lead',reference_lyrics=None):
    from calibration_session import project_data
    from vocal_expression import TempoMap
    from pitch_accuracy import audit_svp_pitch
    import soundfile as sf
    issues=[];result={'issues':issues,'ready':False,'bank':'','lyrics':{'status':'no_reference','candidates':[]}}
    try:
        data=project_data(project);_,track=selected_track(data,track_index)
        notes=track.get('mainGroup',{}).get('notes',[])
        if not notes:raise ValueError('所选轨道没有音符，请选择包含演唱音符的轨道。')
        if track.get('groups'):raise ValueError('所选轨道包含引用音符组，请先在 SV 中合并到主音符组并保存副本。')
        result['bank']=track.get('mainRef',{}).get('database',{}).get('name','')
        if not result['bank']:
            issues.append({'code':'bank_missing','level':'error','message':'尚未指定声库：可从已指定声库的 SVP 读取设置，或在 SV 中选择声库并保存。'})
        clock=TempoMap(data['time']['tempo']);offset=track.get('mainRef',{}).get('blickOffset',0)
        end=max(clock.to_seconds(n['onset']+n['duration']+offset) for n in notes)
        info=sf.info(str(reference))
        if info.channels not in (1,2) or info.samplerate<8000 or info.duration<end-.1:
            raise ValueError(f'参考演唱需为普通单／双声道，从零秒覆盖至 {end:.2f} 秒；当前长度 {info.duration:.2f} 秒。')
        result.update(notes=len(notes),minimum_duration=end,pitch=audit_svp_pitch(track))
        if result['pitch']['status'] in ('invalid','suspicious'):
            issues.append({'code':'pitch_quality','level':'error' if result['pitch']['status']=='invalid' else 'warning',
                'message':'音高数据异常或疑似缺少连续变化。可以只重建音高，保留现有音符与歌词，并另存工程。'})
        elif result['pitch']['status']=='absent':
            issues.append({'code':'pitch_absent','level':'warning','message':'此工程未包含音高曲线。需要参考演唱的连续音高时，可先重建音高；也可保留当前乐谱。'})
        if reference_lyrics is None and job:
            request=json.loads((Path(job)/'request.json').read_text(encoding='utf-8'))
            expected=(Path(job)/'audio'/(voice+'.wav')).resolve()
            if Path(reference).resolve()==expected:
                reference_lyrics=request.get('backing_lyrics','') if voice=='backing' else request.get('lyrics','')
        result['lyrics']=lyric_review(data,track_index,reference_lyrics or '')
        if result['lyrics']['candidates']:
            issues.append({'code':'lyrics_review','level':'warning','message':'参考歌词与乐谱文字存在差异，请按下方时间范围试听、修正。程序不会按字数硬塞音符。'})
        result['ready']=not any(i['level']=='error' for i in issues)
    except (OSError,ValueError,KeyError,TypeError,RuntimeError) as error:
        issues.append({'code':'input_invalid','level':'error','message':str(error)})
    return result


def _infer_pitch(reference,cancel,progress):
    from common import ROOT
    import soundfile as sf
    from pitch_accuracy import prepare_audio,supported_pitch
    from v2m_runtime import configure
    configure(ROOT/'dependencies/vocal2midi2')
    from runtime_devices import prepare_environment
    from inference.API.rmvpe_api import RmvpeTranscriber
    model=ROOT/'dependencies/vocal2midi/experiments/RMVPE/rmvpe.onnx'
    if not model.is_file():raise FileNotFoundError('缺少 RMVPE 音高模型，请检查助手运行库后重试。')
    cancel();progress('重建连续音高：读取参考演唱…')
    audio,rate=sf.read(str(reference),dtype='float32',always_2d=True)
    audio=prepare_audio(audio,rate);cancel();runner=None
    import os
    old_env={key:os.environ.get(key) for key in ('VWA_CPU_THREADS','VWA_DML_ADAPTER','BVWA_CPU_THREADS','BVWA_DML_ADAPTER')}
    try:
        prepare_environment({'midi_device':'cpu','cpu_threads':4,'dml_adapter':'auto'})
        progress('重建连续音高：正在提取（CPU）…')
        runner=RmvpeTranscriber(model,device='cpu',batch_size=1)
        def check():cancel();return False
        return supported_pitch(runner.infer(audio,16000,cancel_checker=check))
    finally:
        if runner:runner.shutdown()
        for key,value in old_env.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


def prepare_copy(project,reference,destination,*,track_index=0,template=None,repair_pitch=False,
                 language=None,cancel=lambda:None,progress=lambda text:None):
    """Prepare a separate score without copying another song's curves or notes."""
    from calibration_session import project_data
    project,reference,destination=Path(project).resolve(),Path(reference).resolve(),Path(destination).resolve()
    if destination.exists():raise FileExistsError('准备目录已存在，请换用新的目录。')
    originals={str(project):file_hash(project),str(reference):file_hash(reference)}
    data=project_data(project);_,track=selected_track(data,track_index)
    if not track.get('mainGroup',{}).get('notes') or track.get('groups'):raise ValueError('请选择主音符组中有音符的演唱轨。')
    if language not in (None,'zh','ja'):raise ValueError('校准语言请选择中文或日语。')
    if template:
        template=Path(template).resolve();originals[str(template)]=file_hash(template)
        settings=next((t.get('mainRef',{}) for t in project_data(template).get('tracks',[])
                       if not t.get('mainRef',{}).get('isInstrumental') and t.get('mainRef',{}).get('database',{}).get('name')),None)
        if settings is None:raise ValueError('所选声库设置工程没有已指定声库的演唱轨，请先在 SV 中设置并保存。')
        ref=track.setdefault('mainRef',{});old_database=ref.get('database',{})
        ref['database']=copy.deepcopy(settings['database'])
        voice=ref.setdefault('voice',{})
        for key,value in settings.get('voice',{}).items():
            if not key.startswith(('tF0','dF0','pF0','fF0','param')):voice[key]=copy.deepcopy(value)
        if language:ref['database'].update(languageOverride='japanese' if language=='ja' else 'mandarin',
                                           phonesetOverride='romaji' if language=='ja' else 'xsampa')
        else:
            for key in ('languageOverride','phonesetOverride'):
                if old_database.get(key):ref['database'][key]=old_database[key]
    cancel();destination.mkdir(parents=True)
    pending=destination/'pitch.pending.svp';source=destination/'input.pending.svp'
    try:
        if repair_pitch:
            from pitch_accuracy import absolute_curve
            from calibration_refinement import dense_points
            from vocal_expression import TempoMap,automation
            from project_exports import library
            import os
            result=_infer_pitch(reference,cancel,progress);cancel()
            single=copy.deepcopy(data);single['tracks']=[copy.deepcopy(track)]
            atomic_json(source,single)
            old_env={k:os.environ.get(k) for k in ('APPDATA','LOCALAPPDATA')}
            try:
                converters,_,SingingTrack,_=library()
                model=converters['svp'].load(source,{'import_pitch':False,'import_instrumental_track':False})
                vocals=[t for t in model.track_list if isinstance(t,SingingTrack)]
                if len(vocals)!=1:raise ValueError('音高重建需要单一主音符组。')
                measurement=absolute_curve(vocals[0],model,result)
                if measurement['points']<3:raise ValueError('没有足够可靠的连续音高；原工程已保留，请检查参考演唱。')
                progress('重建连续音高：写入并检查工程…');cancel()
                converters['svp'].dump(pending,model,{'down_sample':0})
                points=project_data(pending)['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']
            finally:
                for key,value in old_env.items():
                    if value is None:os.environ.pop(key,None)
                    else:os.environ[key]=value
            clock=TempoMap(data['time']['tempo']);offset=track.get('mainRef',{}).get('blickOffset',0)
            track['mainGroup'].setdefault('parameters',{})['pitchDelta']=automation(
                dense_points([(clock.to_seconds(pos),value) for pos,value in zip(points[::2],points[1::2])],cancel=cancel),clock,offset)
            track['mainGroup'].pop('pitchControls',None)
            track.setdefault('mainRef',{}).pop('systemPitchDelta',None)
            for key in ('tF0Left','tF0Right','dF0Left','dF0Right'):track['mainRef'].setdefault('voice',{})[key]=0.
            for note in track['mainGroup']['notes']:
                note['instantMode']=False;note.setdefault('attributes',{})['dF0Vbr']=0.
        # Media references are relative to the original file, never its copy.
        for part in data.get('tracks',[]):
            for ref in [part.get('mainRef',{}),*part.get('groups',[])]:
                audio=ref.get('audio')
                if isinstance(audio,dict) and audio.get('filename') and not Path(audio['filename']).is_absolute():
                    audio['filename']=str((project.parent/audio['filename']).resolve())
        cancel()
        if any(file_hash(p)!=digest for p,digest in originals.items()):raise ValueError('准备期间输入发生变化，请重新检查。')
        output=destination/'prepared.svp';atomic_json(output,data)
        report={'project':str(output),'source':str(project),'repair_pitch':repair_pitch,'template':str(template) if template else None,
                'track_index':track_index,'source_hashes':originals}
        atomic_json(destination/'preparation.json',report);return report
    finally:
        pending.unlink(missing_ok=True);source.unlink(missing_ok=True)


def neutral_project(session,original):
    """Create a neutral comparison with matching notes, pitch, voice and media."""
    from calibration_session import read_session
    from expression_calibration import CONTROLS,_clear_control_offsets
    root=Path(session).resolve().parent;spec=read_session(session)
    receipt=root/'neutral-receipt.json'
    try:
        saved=json.loads(receipt.read_text(encoding='utf-8'));path=root/saved['file']
        if path.parent==root and path.suffix.lower()=='.svp' and saved['source_sha256']==spec['project_sha256'] and file_hash(path)==saved['sha256']:return path
    except (OSError,ValueError,KeyError):pass
    data=copy.deepcopy(original);track=data['tracks'][spec['track_index']];_clear_control_offsets(track)
    for name in CONTROLS:track['mainGroup'].setdefault('parameters',{}).pop(name,None)
    path=root/'neutral-baseline.svp'
    if path.exists():
        import uuid
        path=root/('neutral-baseline-'+uuid.uuid4().hex[:10]+'.svp')
    atomic_json(path,data);atomic_json(receipt,{'file':path.name,'sha256':file_hash(path),'source_sha256':spec['project_sha256']})
    return path


def latest_result(session):
    """Read the last generated result, including dated refits and older sessions."""
    root=Path(session).resolve().parent
    reports=list(root.glob('*.fit-report.json'))+[root/'fit-report.json']
    for path in sorted((p for p in reports if p.is_file()),key=lambda p:p.stat().st_mtime_ns,reverse=True):
        try:
            data=json.loads(path.read_text(encoding='utf-8'));output=Path(data['output']).resolve()
            if output.parent==root and output.suffix.lower()=='.svp' and output.is_file():
                if data.get('neutral'):
                    neutral=Path(data['neutral']).resolve()
                    if neutral.parent!=root or neutral.suffix.lower()!='.svp' or not neutral.is_file():data.pop('neutral',None)
                return data
        except (OSError,ValueError,KeyError,TypeError):continue
    path=root/'calibrated.svp'
    return {'output':str(path)} if path.is_file() else None


def recovery_hint(message):
    if '声道' in message or '气声' in message:return '选择与 SV 相同的气声输出方式，再点“继续等待并拟合”，已有 WAV 可以复用。'
    if '过短' in message or '长度' in message:return '从零秒重新导出七条完整试音到同一目录，保持统一终点，再继续。'
    if '多份' in message or '重复' in message:return '打开导出目录，移走重复试音；保留每种参数一份，然后继续。'
    if '已改变' in message or '已变化' in message:return '工程或参考演唱已修改。点击“重新准备试音”，为新乐谱另建会话。'
    if '超时' in message:return '会话已保留。完成 SV 分轨导出后可以直接继续，不需要重做下载或分离。'
    return '检查输入与导出目录后重试；原工程和已经生成的文件会保留。'
