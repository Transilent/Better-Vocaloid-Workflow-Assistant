"""Business-only bridge between validated style plans and shared GUI drafts."""
from __future__ import annotations
import copy
from agent_style import plan_style
from style_drafts import DraftStore,DraftError,normalize_draft


def read_draft(path, root, input_sha256):
    # The control boundary has already checked the exact original/snapshot SHA.
    snapshot=DraftStore(root).read(input_sha256)
    if snapshot['exists']:
        data=normalize_draft(input_sha256,snapshot['data'])
        if '_sync' in snapshot['data']:data['_sync']=copy.deepcopy(snapshot['data']['_sync'])
        snapshot={**snapshot,'data':data}
    return snapshot


def _proposal_from_regions(data,brief):
    return {'track_indices':list(data['track_indices']),'brief':brief,'regions':[
        {'start':row['start'],'end':row['end'],'preset':row['preset'],'strength':row['strength'],
         'style_pitch':row['pitch'],'detail':copy.deepcopy(row['detail']),
         'style_technique':row.get('technique','none'),
         'technique_strength':row.get('technique_strength',.6),
         'technique_pitch':row.get('technique_pitch',True),
         'rationale':brief,'evidence_refs':[]} for row in data['style_regions']]}


def plan_from_draft(path,root,input_sha256,expected_revision,brief):
    if not isinstance(brief,str) or not brief.strip() or len(brief)>8000:
        raise DraftError('invalid_draft_brief','请提供使用该草稿的背景与理由。')
    snapshot=DraftStore(root).read(input_sha256)
    if not snapshot['exists'] or snapshot['revision']!=expected_revision:
        raise DraftError('draft_conflict','共享草稿已更新，请重新读取当前修订。')
    data=normalize_draft(input_sha256,snapshot['data'])
    if data['mode']!='regions' or not data['style_regions'] or not data['track_indices']:
        raise DraftError('incomplete_draft','请先在共享草稿中选择目标轨道和时间片段。')
    plan=plan_style(path,_proposal_from_regions(data,brief.strip()))
    for original,aligned in zip(data['style_regions'],plan['style_regions']):
        if any(abs(original[k]-aligned[k])>1e-8 for k in ('start','end')):
            raise DraftError('incomplete_note_boundary','草稿片段尚未对齐完整音符，请先调整边缘。')
    return {'plan':plan,'revision':snapshot['revision'],'input_sha256':input_sha256}


def publish_draft(path,root,input_sha256,plan,expected_revision,writer):
    # Recreate the semantic plan: no unvalidated rows can bypass whole notes.
    expected=plan_style(path,plan.get('proposal'))
    if expected!=plan:
        raise DraftError('stale_plan','风格方案与当前工程不一致。')
    store=DraftStore(root)
    previous=store.read(input_sha256)
    data=normalize_draft(input_sha256,previous['data']) if previous['exists'] else {
        'schema_version':1,'input_sha256':input_sha256,'selection':None,'selected_index':-1,
        'tool':{'performance_style':'none','style_strength':.6,'style_pitch':False,
                'style_detail':{'onset':1.,'sustain':1.,'connection':1.,'release':1.},
                'style_technique':'none','technique_strength':.6,'technique_pitch':True},
        'editor_intent':'new'}
    data.pop('_sync',None)
    data.setdefault('next_tool',copy.deepcopy(data['tool']))
    data.update(mode='regions',style_regions=copy.deepcopy(plan['style_regions']),
                track_indices=list(plan['track_indices']))
    if data.get('selected_index',-1)>=len(data['style_regions']):data['selected_index']=-1
    return store.write(input_sha256,expected_revision,data,writer=writer)
