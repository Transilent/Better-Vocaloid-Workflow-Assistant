"""One in-memory tuning transaction: exclusive source, style, final density.

This module does not write projects. Acoustic analysis may use its own feature
cache. Candidate/session I/O belongs to tuning_workflow.
"""
import copy
import hashlib
import json
import math
from pathlib import Path

VERSION=1
REFERENCE=('dynamics','tension','breathiness','voicing')
EXPRESSION=('loudness','tension','breathiness','voicing','gender','toneShift')
DEFAULTS={'expression_mode':'preserve','track_indices':None,'reference_sources':[],
 'reference_dynamics':False,'reference_tension':False,'reference_breathiness':False,'reference_voicing':False,
 'dynamics_strength':.6,'reference_calibration':None,'retained_project':None,'retained_track_index':0,
 'retained_sources':[],'retained_match_policy':'exact','performance_style':'none','style_strength':.6,'style_pitch':False,
 'style_scope':'lead','expression_density':'dense'}


def digest(data):
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


def settings(options):
    if not isinstance(options,dict):raise ValueError('调音选项必须为对象。')
    unknown=set(options)-set(DEFAULTS)
    if unknown:raise ValueError('未知调音选项：'+','.join(sorted(unknown)))
    result={key:copy.deepcopy(options.get(key,value)) for key,value in DEFAULTS.items()}
    mode=result['expression_mode']
    if mode not in ('preserve','reference','calibrated','retained'):raise ValueError('请选择有效的参数来源。')
    for name in REFERENCE:
        if type(result['reference_'+name]) is not bool:raise ValueError('参考参数开关必须为布尔值。')
    for name in ('dynamics_strength','style_strength'):
        value=result[name]
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:
            raise ValueError('调音强度应为0到1的有限数值。')
        result[name]=float(value)
    from style_presets import settings as style_settings
    from calibration_refinement import density_settings
    style_settings(result);density_settings(result)
    indices=result['track_indices']
    if indices is not None and (not isinstance(indices,list) or not indices or
            any(type(x) is not int or x<0 for x in indices) or len(indices)!=len(set(indices))):
        raise ValueError('请选择不重复的有效演唱轨索引。')
    for name in ('reference_sources','retained_sources'):
        if not isinstance(result[name],list):raise ValueError('调音来源映射必须为列表。')
    for name in ('reference_calibration','retained_project'):
        value=result[name]
        if value is not None and (not isinstance(value,str) or not value.strip()):raise ValueError('来源路径不能为空。')
    if type(result['retained_track_index']) is not int or result['retained_track_index']<0:raise ValueError('原参数声部索引无效。')
    has_reference=any(result['reference_'+name] for name in REFERENCE)
    if mode!='reference' and has_reference:raise ValueError('参考映射、校准拟合和沿用参数只能选择一种；请关闭其他来源。')
    if mode!='calibrated' and result['reference_calibration']:raise ValueError('校准配置与当前参数来源冲突。')
    if mode!='retained' and (result['retained_project'] or result['retained_sources']):raise ValueError('沿用工程与当前参数来源冲突。')
    if mode not in ('reference','calibrated') and result['reference_sources']:raise ValueError('当前参数来源不使用参考音频。')
    if mode=='reference' and not has_reference:raise ValueError('请至少选择一种参考演唱参数。')
    if mode=='calibrated' and not result['reference_calibration']:raise ValueError('请选择当前乐谱对应的声库校准配置。')
    if mode=='retained' and not (result['retained_project'] or result['retained_sources']):raise ValueError('请选择原参数工程。')
    if result['retained_match_policy'] not in ('exact','legacy'):raise ValueError('沿用参数匹配策略无效。')
    if mode!='retained' and result['retained_match_policy']!='exact':raise ValueError('当前参数来源不使用沿用匹配策略。')
    return result


def selected_indices(data,options):
    from style_presets import track_indices_for
    indices=options['track_indices']
    indices=track_indices_for(data,options['style_scope']) if indices is None else indices
    if not indices:raise ValueError('工程中没有可处理的演唱轨。')
    for index in indices:
        if index>=len(data['tracks']) or data['tracks'][index].get('mainRef',{}).get('isInstrumental'):
            raise ValueError('所选索引不是有效的演唱轨。')
        track=data['tracks'][index];ref=track.get('mainRef',{})
        if track.get('groups') or ref.get('blickAbsoluteBegin',0)!=0 or ref.get('blickAbsoluteEnd',-1)!=-1:
            raise ValueError('当前调音事务只支持未裁剪的 mainGroup；库组引用请先在编辑器中展开为独立工程副本。')
        group=track.get('mainGroup',{})
        if not isinstance(group.get('notes'),list):raise ValueError('演唱轨缺少音符组。')
    return list(indices)


def sources_for(options,indices):
    rows=options['reference_sources'];mapping={}
    for row in rows:
        if not isinstance(row,dict) or type(row.get('track_index')) is not int:raise ValueError('参考音频缺少明确的工程轨索引。')
        index=row['track_index'];voice=row.get('voice');path=row.get('path')
        if index in mapping or not isinstance(voice,str) or voice not in ('lead','backing','vocals'):
            raise ValueError('参考声部映射重复或名称无效。')
        if not isinstance(path,str) or not path.strip() or not Path(path).is_file():raise ValueError('参考音频不存在。')
        mapping[index]={'voice':voice,'path':Path(path).resolve(),'cache_key':'track-'+str(index)}
    if set(mapping)!=set(indices):raise ValueError('每条所选演唱轨都需要明确对应的参考音频；不能把主唱静默套用到和声。')
    return [mapping[index] for index in indices]


def _protected(data,indices,controls,pitch=False,calibrated=False):
    value=copy.deepcopy(data)
    for index in indices:
        track=value['tracks'][index];group=track.get('mainGroup',{})
        parameters=group.get('parameters',{})
        for name in (*controls,*(('pitchDelta',) if pitch else ())):parameters.pop(name,None)
        if not parameters:group.pop('parameters',None)
        if calibrated:
            ref=track.get('mainRef',{});ref.pop('database',None)
            voice=ref.get('voice',{})
            for name in EXPRESSION:voice.pop('param'+name[0].upper()+name[1:],None)
            if not voice:ref.pop('voice',None)
            for note in group.get('notes',[]):
                attr=note.get('attributes',{})
                for name in EXPRESSION:attr.pop('param'+name[0].upper()+name[1:],None)
                if not attr:note.pop('attributes',None)
    return value


def _has_incomplete(report):
    if isinstance(report,dict):
        if report.get('status') in ('partial','skipped','partial_unsupported') and report.get('reason') not in ('disabled','zero_strength'):return True
        return any(_has_incomplete(value) for value in report.values() if isinstance(value,(dict,list)))
    if isinstance(report,list):return any(_has_incomplete(value) for value in report)
    return False


def _review_calibration_channels(view,sources,profile):
    """A partial measured profile must not silently erase unmeasured authorship."""
    from expression_calibration import read_profile
    spec=read_profile(profile)
    for track,source in zip(view['tracks'],sources):
        row=spec['tracks'].get(source['voice'])
        if row is None:continue
        ref=track.get('mainRef',{});database=ref.get('database',{})
        if database.get('name') and database!=row['database']:
            raise ValueError('声库配置与试音配置不同，请为当前设置重新校准；未替换原声库设置。')
        keys={'param'+name[0].upper()+name[1:] for name in EXPRESSION}
        current_voice={key:value for key,value in ref.get('voice',{}).items() if key not in keys}
        measured_voice={key:value for key,value in row.get('voice',{}).items() if key not in keys}
        if current_voice!=measured_voice:
            raise ValueError('非表现参数的声库设置已改变，请重新试音校准；未清除原设置。')
        measured={p['parameter'] for p in row['probes']}
        missing=set(EXPRESSION)-measured
        parameters=track.get('mainGroup',{}).get('parameters',{})
        authored=[name for name in missing if parameters.get(name,{}).get('points')]
        keys={'param'+name[0].upper()+name[1:] for name in missing}
        voice=track.get('mainRef',{}).get('voice',{})
        offsets=[key for key in keys if voice.get(key,0)!=0]
        for note in track.get('mainGroup',{}).get('notes',[]):
            offsets.extend(key for key in keys if note.get('attributes',{}).get(key,0)!=0)
        if authored or offsets:
            raise ValueError('当前校准缺少部分原调音通道的试音，不能安全清除旧参数：'+
                             '、'.join(sorted(set(authored+offsets)))+'。请补齐试音或选择保留原表现。')


def _review_reference_offsets(data,indices,options):
    """Relative reference mapping cannot infer an authored bias's baseline."""
    names=[('loudness' if name=='dynamics' else name) for name in REFERENCE if options['reference_'+name]]
    conflicts=[]
    for index in indices:
        track=data['tracks'][index]
        for name in names:
            key='param'+name[0].upper()+name[1:]
            if track.get('mainRef',{}).get('voice',{}).get(key,0)!=0:
                conflicts.append({'track_index':index,'parameter':name,'location':'voice'})
            for note_index,note in enumerate(track.get('mainGroup',{}).get('notes',[])):
                if note.get('attributes',{}).get(key,0)!=0:
                    conflicts.append({'track_index':index,'parameter':name,'location':'note','note_index':note_index})
    if conflicts:
        first=conflicts[0]
        raise ValueError('参考映射的所选通道已含原声部或音符偏置（轨'+str(first['track_index'])+' / '+first['parameter']+
                         '），不能静默叠加新曲线。请选择保留原表现，或为当前设置重新实测校准。')


def finalize_project(output,indices,mode,cancel):
    from calibration_refinement import finalize_expression
    view={'time':output['time'],'tracks':[output['tracks'][i] for i in indices]}
    report=finalize_expression(view,mode=mode,cancel=cancel)
    for row in report['tracks']:row['track_index']=indices[row['track_index']]
    return report


def process_project(data,options,*,cancel=lambda:None,progress=lambda text:None,cache=None,finalize=True):
    """Pure project transaction. No intermediate SVP serialize/read/overwrite."""
    if not isinstance(data,dict) or not isinstance(data.get('tracks'),list) or not isinstance(data.get('time'),dict):
        raise ValueError('请选择有效的SVP工程。')
    options=settings(options);indices=selected_indices(data,options);source_sha=digest(data)
    output=copy.deepcopy(data);mode=options['expression_mode'];cancel()
    report={'schema':VERSION,'mode':mode,'track_indices':indices,'stages':[],
            'source_content_sha256':source_sha,'semantic_model_used':False,'synthesized':False,'listened':False,
            'acoustic_improvement_verified':False,'expression_source_exclusive':True}
    primary={'status':'preserved','mode':mode};strength=options['dynamics_strength']
    if mode in ('reference','calibrated'):
        sources=sources_for(options,indices)
        if strength>0:
            progress('处理参数来源：'+mode)
            view={'time':copy.deepcopy(output['time']),'tracks':[copy.deepcopy(output['tracks'][i]) for i in indices]}
            if mode=='reference':
                from vocal_expression import apply_project
                _review_reference_offsets(output,indices,options)
                transformed,primary=apply_project(view,sources,strength,cancel,
                    dynamics=options['reference_dynamics'],
                    timbre=tuple(name for name in ('tension','breathiness','voicing') if options['reference_'+name]),
                    feature_cache=cache,density_mode='original',finalize=False)
            else:
                from expression_calibration import apply_project
                _review_calibration_channels(view,sources,options['reference_calibration'])
                transformed,primary=apply_project(view,sources,options['reference_calibration'],strength,cancel,
                    cache=cache,progress=progress,density_mode='original',finalize=False)
            for index,track in zip(indices,transformed['tracks']):output['tracks'][index]=track
        else:primary={'status':'not_selected','reason':'zero_strength','mode':mode,'strength':strength,
                      'tracks':[],'voicebank_calibrated':False}
    elif mode=='retained':
        from calibration_session import project_data
        from calibration_workflow import retain_project
        rows=options['retained_sources'] or [{'track_index':i,'project':options['retained_project'],
                'source_track_index':options['retained_track_index']} for i in indices]
        if {row.get('track_index') for row in rows}!=set(indices) or len(rows)!=len(indices):
            raise ValueError('沿用参数必须明确覆盖每条所选演唱轨，不能重复或漏配。')
        tracks=[]
        for row in rows:
            cancel();index=row['track_index'];path=Path(row['project']).resolve()
            prior=project_data(path)
            # retain_project indexes vocal tracks ordinally, not raw project indices.
            vocal_indices=[i for i,t in enumerate(output['tracks']) if not t.get('mainRef',{}).get('isInstrumental')]
            policy={} if options['retained_match_policy']=='exact' else {'match_policy':'legacy'}
            output,result=retain_project(output,prior,track_index=vocal_indices.index(index),
                source_track_index=row.get('source_track_index',0),**policy)
            tracks.append({'track_index':index,'voice':row.get('voice'),'result':result})
        primary={'status':'applied','mode':mode,'tracks':tracks,'new_fit':False}
    primary_controls=EXPRESSION if mode in ('calibrated','retained') else tuple(
        ('loudness' if name=='dynamics' else name) for name in REFERENCE if options['reference_'+name])
    if _protected(data,indices,primary_controls,False,mode in ('calibrated','retained'))!=_protected(output,indices,primary_controls,False,mode in ('calibrated','retained')):
        raise ValueError('参数来源处理改变了未选择的通道或工程对象，未提交。')
    replacements=[]
    for index in indices:
        before=data['tracks'][index].get('mainGroup',{}).get('parameters',{})
        after=output['tracks'][index].get('mainGroup',{}).get('parameters',{})
        for name in primary_controls:
            old,new=before.get(name),after.get(name)
            if old!=new:
                replacements.append({'track_index':index,'parameter':name,
                    'before_sha256':digest(old),'after_sha256':digest(new),
                    'points_before':len((old or {}).get('points',[]))//2,
                    'points_after':len((new or {}).get('points',[]))//2,
                    'operation':'explicit_primary_source_replacement','recoverable_from_input':True})
    report['primary_curve_replacements']=replacements
    report['primary']=primary;report['stages'].append('primary')
    style={'status':'not_selected','pitch_changed':False}
    if options['performance_style']!='none' and options['style_strength']>0:
        progress('叠加所选风格增量')
        from style_presets import apply_style
        output,style=apply_style(output,options['performance_style'],strength=options['style_strength'],
            track_indices=indices,density_mode='original',pitch_enabled=options['style_pitch'],cancel=cancel,finalize=False)
        report['stages'].append('style')
    report['style']=style
    progress('统一保形加密与保护校验')
    density=finalize_project(output,indices,options['expression_density'],cancel) if finalize else {
        'status':'deferred','mode':options['expression_density'],'added_points':0,'unsupported_nonlinear_controls':0}
    report['expression_density']=density
    if finalize:report['stages'].append('density')
    # Primary calibration/retention can legitimately install the selected bank's
    # expression offsets; all score/pitch/retake/unknown objects still compare.
    if _protected(data,indices,EXPRESSION,options['style_pitch'],mode in ('calibrated','retained'))!=_protected(output,indices,EXPRESSION,options['style_pitch'],mode in ('calibrated','retained')):
        raise ValueError('调音处理改变了保护范围之外的工程内容，未提交。')
    if digest(data)!=source_sha:raise ValueError('调音处理意外修改了输入对象，未提交。')
    cancel();changed=digest(output)!=source_sha
    incomplete=(mode not in ('preserve','retained') and strength>0 and _has_incomplete(primary)) or _has_incomplete(style) or bool(density['unsupported_nonlinear_controls'])
    report.update(status='partial' if incomplete else 'applied' if changed else 'unchanged',
                  output_content_sha256=digest(output),changed=changed,
                  protected_content_unchanged=True,original_input_unchanged=True,
                  notes_and_timing_unchanged=True,pitch_changed=bool(style.get('pitch_changed')),
                  note_expression_offsets_may_change=mode in ('calibrated','retained'),
                  project_writes=0,final_density_passes=int(finalize),
                  calibration_metrics_scope='primary_stage_before_style' if mode=='calibrated' else None)
    return output,report


def primary_report(output,report):
    """Expose final curve counts without changing the primary-stage evidence."""
    from vocal_expression import curve_statistics
    compatible=copy.deepcopy(report['primary'])
    compatible.update(expression_density=report['expression_density'],deferred=[])
    for index,row in zip(report['track_indices'],compatible.get('tracks',[])):
        parameters=output['tracks'][index]['mainGroup'].get('parameters',{})
        if report['mode']=='reference':
            for name,state in row.get('parameter_status',{}).items():
                stats=curve_statistics(parameters,name,require_variation=state.get('status')=='applied')
                state.update(stats)
                measured=row if name=='loudness' else row.get('timbre',{}).get('parameters',{}).get(name,{})
                if measured.get('status')=='applied':measured['written_points']=stats['point_count']
        elif report['mode']=='calibrated' and row.get('status')=='applied':
            row['written_points']={name:len(parameters.get(name,{}).get('points',[]))//2 for name in row.get('written_points',{})}
    return compatible


def legacy_export(data,job,request,tracks,*,cancel=lambda:None):
    """Adapt old requests deterministically, without sequential source overwrites.

    Old explicit profiles take precedence over retained automation, then reference
    mapping. New preparation requests defer tuning but still retain authored
    automation when regenerating compatible notes.
    """
    from vocal_expression import expression_settings
    dynamics,strength,timbre=expression_settings(request)
    indices=[i for i,t in enumerate(data['tracks']) if not t.get('mainRef',{}).get('isInstrumental')]
    options={'track_indices':indices,'expression_density':request.get('expression_density','dense')}
    profile=request.get('reference_calibration') if not request.get('prepare_only') and strength>0 else None
    retained=request.get('retained_expression')
    if profile:
        from common import app_path
        options.update(expression_mode='calibrated',reference_calibration=str(app_path(profile)),dynamics_strength=strength)
    elif retained:
        from calibration_workflow import retained_signature
        retained_signature(Path(job),request)
        voices=[row['voice'] for row in tracks]
        options.update(expression_mode='retained',retained_match_policy='legacy',retained_sources=[{'track_index':indices[voices.index(voice)],
            'project':str((Path(job)/row['file']).resolve()),'source_track_index':row['track_index'],'voice':voice}
            for voice,row in retained['tracks'].items()])
        # Some tasks retain only an authored lead, not all generated voices.
        options['track_indices']=[row['track_index'] for row in options['retained_sources']]
    elif not request.get('prepare_only') and (dynamics or timbre) and strength>0:
        options.update(expression_mode='reference',reference_dynamics=dynamics,dynamics_strength=strength,
                       **{'reference_'+name:name in timbre for name in ('tension','breathiness','voicing')})
    mode=options.get('expression_mode','preserve')
    if mode in ('reference','calibrated'):
        options['reference_sources']=[{'track_index':i,'voice':row['voice'],
            'path':str(Path(job)/'audio'/(row['voice']+'.wav'))} for i,row in zip(indices,tracks)]
    output,report=process_project(data,options,cancel=cancel,cache=Path(job)/'temp/reference-features',finalize=False)
    # Retaining only one part must still allow the requested final density on
    # other generated tracks. Their untouched source curves remain authoritative.
    density=finalize_project(output,indices,options['expression_density'],cancel)
    report.update(expression_density=density,final_density_passes=1,output_content_sha256=digest(output),changed=digest(output)!=digest(data))
    report['stages'].append('density')
    if density['unsupported_nonlinear_controls']:report['status']='partial'
    elif report['status']!='partial':report['status']='applied' if digest(output)!=digest(data) else 'unchanged'
    details={'expression_density':density,'tuning_transaction':report}
    if mode in ('reference','calibrated'):
        details['reference_dynamics' if mode=='reference' else 'calibration_fit']=primary_report(output,report)
    elif mode=='retained':details['retained_expression']={row['voice']:row['result'] for row in report['primary']['tracks']}
    details['parameter_source_policy']='calibrated_then_retained_then_reference_exclusive'
    return output,details
