"""Execution validation shared by tuning, preset runs, recovery and registration.

The workflow owns locking and publication. No Qt, synthesis or acoustic
analysis occurs here; it validates options, project and dependency identities.
"""
import copy
from pathlib import Path
from common import file_hash
from tuning_engine import digest, settings, selected_indices, sources_for


def normalize_options(options):
    result=settings(options)
    for key in ('reference_calibration','retained_project'):
        if result[key]:result[key]=str(Path(result[key]).expanduser().resolve())
    for row in result['reference_sources']:
        if isinstance(row,dict) and isinstance(row.get('path'),str):row['path']=str(Path(row['path']).expanduser().resolve())
    for row in result['retained_sources']:
        if isinstance(row,dict) and isinstance(row.get('project'),str):row['project']=str(Path(row['project']).expanduser().resolve())
    return result


def project_context(spec):
    return {'path':spec['source'],'sha256':spec['input_sha256'],
            'tracks':copy.deepcopy(spec['tracks']),'candidate_origin':copy.deepcopy(spec.get('input_origin'))}


def preset_context(binding,spec,options,data):
    if binding is None:return None
    from tuning_preset_documents import plan_application,validate_plan
    project=project_context(spec)
    checked=validate_plan(copy.deepcopy(binding),project=project)
    if normalize_options(checked['options'])!=options:
        raise ValueError('预设绑定与本次实际调音参数不一致，请重新预览应用。')
    if spec.get('format')=='ustx':
        from ustx_tuning import selected_indices as select_ustx
        indices=select_ustx(data,options)
    else:
        indices=selected_indices(data,options)
    mode=options['expression_mode']
    local={'vocal_tracks':indices}
    if checked['bindings'].get('confirm_candidate_baseline') is True:local['confirm_candidate_baseline']=True
    if mode in ('reference','calibrated'):
        sources=sources_for(options,indices)
        local['reference_audio']=copy.deepcopy(options['reference_sources'])
        if mode=='calibrated':
            local['calibration_profile']=options['reference_calibration']
            _check_calibrated_project(data,indices,sources,options['reference_calibration'])
    elif mode=='retained':
        local['retained_project']=copy.deepcopy(options['retained_sources'])
        if not local['retained_project'] and options['retained_project']:
            local['retained_project']=[{'track_index':i,'project':options['retained_project'],
                                       'source_track_index':options['retained_track_index']} for i in indices]
    context=plan_application(checked['document'],project,local,raw_document=checked['document_text'])
    context=validate_plan(context,project=project)
    # A display diff must not create a new candidate for the same parameters.
    context['changes']=[]
    if normalize_options(context['options'])!=options:raise ValueError('工程绑定投影与执行参数不一致。')
    return context


def _check_calibrated_project(data,indices,sources,profile):
    from expression_calibration import read_profile,score_identity
    from tuning_engine import _review_calibration_channels
    spec=read_profile(profile)
    for index,source in zip(indices,sources):
        row=spec['tracks'].get(source['voice'])
        if row is None:raise ValueError('校准配置没有绑定声部的试音，请补齐后再应用。')
        if row['score_sha256']!=score_identity(data['tracks'][index],data):
            raise ValueError('校准试音对应的音符或速度表已改变，请为当前工程重新校准。')
    view={'time':data['time'],'tracks':[data['tracks'][i] for i in indices]}
    _review_calibration_channels(view,sources,profile)


def preset_dependency(binding):
    if binding is None:return None
    return {'preset':copy.deepcopy(binding['preset']),'binding_sha256':digest(binding)}


def receipt_binding_matches(receipt,dependencies):
    expected=dependencies.get('preset');binding=receipt.get('preset_binding')
    if expected is None:return binding is None
    try:
        return (preset_dependency(binding)==expected and binding['input_sha256']==receipt.get('source_sha256')
                and normalize_options(binding['options'])==receipt.get('options'))
    except (ValueError,TypeError,KeyError):return False


def dependencies(options,contributors,root):
    paths=[Path(row['path']) for row in options['reference_sources']]
    if options['retained_project']:paths.append(Path(options['retained_project']))
    paths.extend(Path(row['project']) for row in options['retained_sources'])
    profile=options['reference_calibration']
    if profile:
        from expression_calibration import read_profile
        spec=read_profile(profile);paths.append(Path(profile))
        for row in spec['tracks'].values():paths.extend((Path(row['baseline']),*(Path(p['audio']) for p in row['probes'])))
    return {'files':{str(p.resolve()):file_hash(p) for p in sorted(set(paths))},
            'code':{name:file_hash(root/name) for name in contributors}}
