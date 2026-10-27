"""Stable, actionable errors shared by the SDK boundary and business workers."""
from __future__ import annotations

REMEDIATION={
    'monophonic_overlap':['vwa_score_review','vwa_score_patch_plan','vwa_workflow_rebind_score'],
    'export_converter_rejected':['vwa_workflow_rebind_score','vwa_score_review'],
    'style_region_overlap':['vwa_context_read','vwa_style_plan'],
    'lyric_alignment_broken':['vwa_lyrics_review','vwa_score_patch_plan'],
    'model_missing':['vwa_preparation_plan'],
    'preparation_stage_missing':['vwa_jobs_get','vwa_preparation_reuse_plan'],
    'retry_requires_rebind':['vwa_workflow_rebind_score'],
    'stale_artifact':['vwa_artifacts_inspect'],
    'stale_workflow':['vwa_workflow_clone'],
    'workflow_conflict':['vwa_workflow_get'],
    'workflow_closed':['vwa_workflow_clone'],
}

def error_payload(error):
    message=str(error)
    code=getattr(error,'code',None) or 'business_error'
    if code in ('business_error','invalid_request'):
        if '整音符对齐后风格片段' in message and '重叠' in message:code='style_region_overlap'
        elif 'LibreSVIP 无法导入' in message or 'Notes overlapped near bar' in message:code='export_converter_rejected'
    result={'code':code,'message':message,
        'component':getattr(error,'component',None) or 'vwa',
        'retryable':bool(getattr(error,'retryable',False)),
        'remediation':getattr(error,'remediation',None) or REMEDIATION.get(code,[])}
    if isinstance(getattr(error,'details',None),dict):result['details']=error.details
    return result
