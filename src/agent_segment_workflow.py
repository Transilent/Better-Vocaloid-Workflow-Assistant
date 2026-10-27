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

def style_review(workflow,identifier,segment_id=None):
    value=workflow._read(identifier)
    if value['stage']!='project_ready' or not workflow._export_accepted(value):
        raise ControlError('wrong_stage','先完成工程导出监督，再逐段核对风格。')
    segment=select(value,'style',segment_id)
    artifact=workflow.control.artifact(value['project_artifact_id'])
    result=workflow.control.call_worker('style_segment_context',{'path':artifact['path'],'segment':segment})
    return {'workflow_id':identifier,'revision':value['revision'],'artifact_id':artifact['id'],
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
