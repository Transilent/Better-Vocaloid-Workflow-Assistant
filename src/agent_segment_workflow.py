"""Sequential Agent review over bounded MIDI and style segments."""
from __future__ import annotations
import copy
from agent_control import ControlError

def enabled(value):return value.get('request',{}).get('review_mode')=='segments'

def make_segments(control,artifact_id,kind):
    artifact=control.artifact(artifact_id)
    return control.call_worker('segment_plan',{'path':artifact['path'],'kind':kind})

def progress(value,kind):
    plan=value.get(kind+'_segments') or {}
    done=value.get(kind+'_segment_decisions') or {}
    rows=plan.get('segments',[])
    checked=[row for row in rows if row['fingerprint'] in done]
    pending=next((row for row in rows if row['fingerprint'] not in done),None)
    return {'mode':'segments','kind':kind,'checked':len(checked),'total':len(rows),
        'complete':len(checked)==len(rows) and bool(rows),'source_sha256':plan.get('source_sha256'),
        'current':{k:v for k,v in pending.items() if k!='note_ids'} if pending else None,
        'instructions':'逐段读取并记录判断；修改后回读本段确认，再推进下一段。'}

def select(value,kind,segment_id=None):
    plan=value.get(kind+'_segments') or {}
    rows=plan.get('segments',[])
    done=value.get(kind+'_segment_decisions') or {}
    if segment_id is None:
        row=next((row for row in rows if row['fingerprint'] not in done),None)
    else:row=next((row for row in rows if row['id']==segment_id),None)
    if row is None:raise ControlError('segment_not_found','当前版本没有待查片段，请重读分段进度。')
    return copy.deepcopy(row)

def authorize_edits(segment,note_edits,lyric_edits,structure_edits):
    allowed=set(segment['note_ids'])
    for edit in (note_edits or [])+(lyric_edits or [])+(structure_edits or []):
        if edit.get('operation')=='insert':
            if edit.get('track_index')!=segment['track_index'] or edit.get('channel')!=segment.get('channel'):
                raise ControlError('segment_edit_outside_scope','插入音符需属于当前片段声部。')
            # Tick boundaries are provided by the MIDI segment worker.
            tick=edit.get('onset_tick')
            if type(tick) is not int or not segment.get('start_tick',0)<=tick<segment.get('end_tick',0):
                raise ControlError('segment_edit_outside_scope','插入位置不在当前片段；请先定位对应片段。')
        elif edit.get('note_id') not in allowed:
            raise ControlError('segment_edit_outside_scope','修改只能引用当前片段的音符；相邻上下文为只读。')

def consume_midi(value,result):
    completion=result.get('segment_completion')
    if not completion:raise ControlError('segment_result_missing','核对结果缺少片段身份。')
    previous=value.get('midi_segment_decisions') or {}
    plan=result['segment_plan']
    valid={row['fingerprint'] for row in plan['segments']}
    decisions={key:copy.deepcopy(row) for key,row in previous.items() if key in valid}
    if not completion['edited'] and completion['fingerprint'] in valid:
        decisions[completion['fingerprint']]={'segment_id':completion['segment_id'],
            'rationale':result['rationale'],'source_sha256':result['source_sha256'],'decision':'checked'}
    conflicts=result.get('monophonic_conflicts',[])
    for row in plan['segments']:
        if any(set(conflict.get('note_ids',[]))&set(row['note_ids']) for conflict in conflicts):
            decisions.pop(row['fingerprint'],None)
    complete=bool(plan['segments']) and all(row['fingerprint'] in decisions for row in plan['segments'])
    return {'stage':'reviewed' if complete else 'review','midi_segments':plan,
        'midi_segment_decisions':decisions,'segment_blockers':conflicts,
        'last_segment_result':{'edited':completion['edited'],'requires_reread':completion['edited'],
            'segment_id':completion['segment_id']}}

def _style_result_read(workflow, value, segment_id=None):
    """Read the already-applied style result without inventing a review gate."""
    from pathlib import Path
    base = {
        'schema': 'vwa-workflow-style-result-v1',
        'workflow_id': value['id'],
        'revision': value['revision'],
        'stage': value['stage'],
        'review_phase': 'result',
        'stage_semantics': 'ready 阶段只回读已应用的风格结果，不再进行逐段决策。',
        'segment_context_applicable': False,
        'requested_segment_id': segment_id,
    }
    from agent_project_state import current_style
    binding = current_style(value)
    if binding is None:
        return {**base, 'available': False, 'applied': False,
                'reason': 'workflow_ready_without_style_apply',
                'instructions': '当前工作流在未应用风格的情况下已完成；没有风格结果可回读。'}
    operation_id = binding.get('operation_id')
    if not isinstance(operation_id, str) or not operation_id:
        return {**base, 'available': False, 'applied': True,
                'reason': 'style_operation_id_missing',
                'instructions': '工作流标记了风格完成，但缺少可核对的风格操作身份。'}
    try:
        operation = workflow.control.operation_get(operation_id)
    except (ControlError, KeyError):
        return {**base, 'available': False, 'applied': True,
                'operation_id': operation_id, 'reason': 'style_operation_evidence_missing',
                'instructions': '风格结果状态存在，但操作证据已不可读；请结合交付回执核对。'}
    operation_request = operation.get('request') if isinstance(operation.get('request'), dict) else {}
    result = operation.get('result') if isinstance(operation.get('result'), dict) else {}
    rendered = result.get('receipt')
    summary = {**base, 'available': False, 'applied': True,
               'operation_id': operation_id, 'operation_state': operation.get('state')}
    if operation_request.get('action') != 'style_render' or operation.get('state') != 'succeeded':
        return {**summary, 'reason': 'style_operation_not_successful',
                'instructions': '仅完成并由工作流消费的风格操作可回读；当前操作证据未成功。'}
    if not isinstance(rendered, dict):
        return {**summary, 'reason': 'style_render_receipt_missing',
                'instructions': '风格操作缺少渲染回执，不能声称结果已核验。'}
    try:
        selected = workflow.control.artifact(value['project_artifact_id'])
    except (ControlError, KeyError):
        return {**summary, 'reason': 'style_result_artifact_missing',
                'instructions': '当前工作流的最终工程已不可读。'}
    candidate_path = rendered.get('candidate_path') or rendered.get('output')
    candidate_sha = rendered.get('candidate_sha256') or rendered.get('output_sha256')
    if (binding.get('artifact_id') != selected.get('id') or
            ('sha256' in binding and binding['sha256'] != selected.get('sha256'))):
        return {**summary, 'reason': 'style_result_identity_mismatch',
                'instructions': '风格结果身份与当前最终工程不一致，请重新读取工作流。'}
    path_matches = isinstance(candidate_path, str) and candidate_path and (
        Path(candidate_path).resolve() == Path(selected['path']).resolve())
    if not path_matches or candidate_sha != selected.get('sha256'):
        return {**summary, 'reason': 'style_result_identity_mismatch',
                'artifact_id': selected.get('id'), 'expected_sha256': selected.get('sha256'),
                'candidate_sha256': candidate_sha,
                'instructions': '风格回执与当前最终工程不一致，请重新读取工作流或交付证据。'}
    report = rendered.get('report')
    if not isinstance(report, dict):
        tuning = rendered.get('tuning_result')
        report = tuning.get('report') if isinstance(tuning, dict) else None
    report = report if isinstance(report, dict) else {}
    style_report = report.get('style') if isinstance(report.get('style'), dict) else {}
    regions = style_report.get('regions') or report.get('style_regions')
    reason_counts = rendered.get('partial_reason_counts')
    if not isinstance(reason_counts, dict):
        reason_counts = report.get('partial_reason_counts') if isinstance(report.get('partial_reason_counts'), dict) else {}
    partial_status = report.get('status') or rendered.get('status')
    style_status = style_report.get('status') or partial_status
    return {
        **base,
        'available': True,
        'applied': True,
        'operation_id': operation_id,
        'operation_state': operation.get('state'),
        'artifact_id': selected.get('id'),
        'result_status': partial_status,
        'style_status': style_status,
        'partial': partial_status == 'partial',
        'style_partial': style_status == 'partial',
        'reason': report.get('reason') or style_report.get('reason'),
        'partial_reason_counts': copy.deepcopy(reason_counts),
        'partial_remediation': copy.deepcopy(rendered.get('partial_remediation', [])),
        'partial_advice': copy.deepcopy(rendered.get('partial_advice', [])),
        'applied_controls': style_report.get('applied_controls', report.get('applied_controls', 0)),
        'skipped_controls': style_report.get('skipped_controls', report.get('skipped_controls', 0)),
        'protected_note_count': report.get('protected_note_count', style_report.get('protected_note_count', 0)),
        'region_count': len(regions) if isinstance(regions, list) else None,
        'source_sha256': rendered.get('source_sha256'),
        'candidate_sha256': candidate_sha,
        'source_unchanged': rendered.get('source_unchanged') is True,
        'receipt': {'path': rendered.get('receipt'), 'sha256': rendered.get('receipt_sha256')},
        'semantic_model_used': False,
        'synthesized': False,
        'listened': False,
        'instructions': '只回读已应用结果；如需修改，请从当前最终工程建立新的显式风格方案。',
    }


def style_review(workflow,identifier,segment_id=None):
    value=workflow._read(identifier)
    if value['stage']=='ready':
        return _style_result_read(workflow,value,segment_id)
    if value['stage']=='styling':
        operation_id=value.get('operation_id')
        operation=workflow.control.operation_get(operation_id) if operation_id else {}
        return {'schema':'vwa-workflow-style-result-v1','workflow_id':identifier,
            'revision':value['revision'],'stage':'styling','review_phase':'apply_in_progress',
            'stage_semantics':'风格操作正在执行；此阶段没有逐段上下文可提交。',
            'available':False,'applied':False,'operation_id':operation_id,
            'operation_state':operation.get('state'),
            'next':{'tool':'vwa_wait','arguments':{'operation_id':operation_id},
                    'reason':'style_render_running'}}
    if value['stage']!='project_ready':
        raise ControlError('wrong_stage',
            'workflow_style_review 用于 project_ready 的逐段检查，或 ready 后回读风格结果；当前阶段为 '+value['stage']+'。')
    if not workflow._export_accepted(value):
        raise ControlError('wrong_stage','工程已生成，但导出监督尚未完成；先核对并接受导出报告。')
    if not enabled(value):
        raise ControlError('style_segment_review_not_applicable',
            '逐段风格上下文仅在 review_mode=segments 的 project_ready 工作流中可用。')
    segment=select(value,'style',segment_id)
    artifact=workflow.control.artifact(value['project_artifact_id'])
    result=workflow.control.call_worker('style_segment_context',{'path':artifact['path'],'segment':segment})
    return {'workflow_id':identifier,'revision':value['revision'],'artifact_id':artifact['id'],
        'stage':'project_ready','review_phase':'segment_checks',
        'stage_semantics':'仅逐段检查下一段；所有段完成后回到 workflow_advance 汇总并生成风格。',
        'segment':{k:v for k,v in segment.items() if k!='note_ids'},'context':result,
        'progress':progress(value,'style'),'decision_tool':'vwa_workflow_style_decide',
        'instructions':'只判断本段：保留原表现，或给本段完整音符的风格。相邻上下文不属于可编辑范围。'}

def style_decide(workflow,identifier,revision,segment_id,rationale,proposal=None):
    if not isinstance(rationale,str) or not rationale.strip() or len(rationale)>4000:
        raise ControlError('segment_rationale_required','每段需说明风格选择或保留原表现的理由。')
    with workflow._lock(identifier):
        value=workflow._expected(identifier,revision)
        if value['stage']!='project_ready' or not workflow._export_accepted(value):
            raise ControlError('wrong_stage','先完成工程监督，再提交本段风格判断。')
        segment=select(value,'style',segment_id)
        normalized=None
        if proposal is not None:
            if proposal.get('track_indices')!=[segment['track_index']]:
                raise ControlError('segment_style_outside_scope','风格目标需仅包含本段的轨道。')
            plan=workflow.control.make_plan('style',value['project_artifact_id'],proposal=proposal)
            references={note for row in plan['plan']['aligned_ranges'] for note in row['note_ids']}
            if not references or not references<=set(segment['note_ids']):
                raise ControlError('segment_style_outside_scope','风格选区包含本段之外的完整音符，请按当前片段缩小范围。')
            normalized=plan['plan']['proposal']
        decisions=copy.deepcopy(value.get('style_segment_decisions') or {})
        decisions[segment['fingerprint']]={'segment_id':segment['id'],'rationale':rationale.strip(),
            'decision':'apply_style' if normalized else 'keep_original','proposal':normalized}
        workflow._write(value,'style_segment_checked',style_segment_decisions=decisions)
    return workflow.get(identifier)

def combined_style(value):
    if not progress(value,'style')['complete']:
        raise ControlError('style_segments_incomplete','逐段风格检查尚未完成，请先处理下一段。')
    decisions=value.get('style_segment_decisions') or {}
    proposals=[decisions[row['fingerprint']].get('proposal') for row in value['style_segments']['segments']]
    proposals=[row for row in proposals if row]
    if not proposals:return None
    if len({tuple(row['track_indices']) for row in proposals})>1:
        raise ControlError('segmented_multi_track_style','当前风格引擎使用全局目标轨。请在本次生成中仅为一个演唱轨设置风格，其余轨保留原表现，避免跨轨误应用。')
    return {'brief':value['request'].get('brief') or '逐段风格判断',
        'track_indices':sorted({index for row in proposals for index in row['track_indices']}),
        'regions':sorted([copy.deepcopy(region) for row in proposals for region in row['regions']],key=lambda r:r['start'])}
