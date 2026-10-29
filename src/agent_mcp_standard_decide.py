"""Typed review and style-decision actions for the standard MCP profile.

This adapter deliberately keeps every original plan, apply, draft, and workflow
operation explicit. It never approves evidence, starts an operation that was
not selected by the caller, renders a style, advances beyond a review/report
gate, or synthesizes audio. Backends remain responsible for caller identity,
artifact roots, SHAs, workflow revisions, shared-draft CAS, idempotency keys,
and operation receipts. Exact lyric-event move/delete edits are exposed only
through the standalone lyrics plan/apply actions until workflow event
authorization and identity rebinding are implemented.
"""
from __future__ import annotations

from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from agent_control import ControlError
from agent_mcp_models import (
    LyricsEdit,
    PronunciationEdit,
    PronunciationTarget,
    Result,
    ScoreEdit,
    StrictModel,
    StructureEdit,
    StyleProposal,
)


# Every branch writes a plan, shared draft, workflow record, or operation.
# Draft publication and revision-based workflow changes can conflict on replay,
# so replay is not advertised as universally idempotent.
MAX_WAIT_MS = 60_000
DECIDE_ANNOTATIONS = ToolAnnotations(
    title='Submit explicit review decisions and plans',
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


class ScorePatchPlan(StrictModel):
    action: Literal['score_patch_plan']
    artifact_id: str
    expected_sha256: str
    edits: Annotated[list[ScoreEdit], Field(min_length=1, max_length=4096)]
    move_attached_lyrics: bool = False


class ScorePatchApply(StrictModel):
    action: Literal['score_patch_apply']
    plan_id: str
    idempotency_key: str


class StylePlan(StrictModel):
    action: Literal['style_plan']
    artifact_id: str
    proposal: StyleProposal
    detail: Literal['summary', 'full'] = 'summary'


class LyricsPatchPlan(StrictModel):
    action: Literal['lyrics_patch_plan']
    artifact_id: str
    expected_sha256: str
    edits: Annotated[list[LyricsEdit], Field(min_length=1, max_length=4096)]


class LyricsPatchApply(StrictModel):
    action: Literal['lyrics_patch_apply']
    plan_id: str
    idempotency_key: str


class StructurePatchPlan(StrictModel):
    action: Literal['structure_patch_plan']
    artifact_id: str
    expected_sha256: str
    edits: list[StructureEdit]


class StructurePatchApply(StrictModel):
    action: Literal['structure_patch_apply']
    plan_id: str
    idempotency_key: str


class PronunciationPatchPlan(StrictModel):
    action: Literal['pronunciation_patch_plan']
    artifact_id: str
    expected_sha256: str
    edits: Annotated[list[PronunciationEdit], Field(min_length=1, max_length=512)]


class PronunciationPatchApply(StrictModel):
    action: Literal['pronunciation_patch_apply']
    plan_id: str
    idempotency_key: str


class DraftsPublish(StrictModel):
    action: Literal['drafts_publish']
    plan_id: str
    expected_revision: str | None = None


class DraftsPlan(StrictModel):
    action: Literal['drafts_plan']
    artifact_id: str
    expected_revision: str
    brief: str


class WorkflowStyleDecide(StrictModel):
    action: Literal['workflow_style_decide']
    workflow_id: str
    expected_revision: int
    segment_id: str
    rationale: str
    proposal: StyleProposal | None = None


class WorkflowApplyReview(StrictModel):
    action: Literal['workflow_apply_review']
    workflow_id: str
    expected_revision: int
    rationale: str
    note_edits: list[ScoreEdit] | None = None
    lyric_edits: list[LyricsEdit] | None = None
    structure_edits: list[StructureEdit] | None = None
    move_attached_lyrics: bool = False
    segment_id: str | None = None
    dry_run: bool = False
    run_until_gate: bool = False
    wait_ms: Annotated[int, Field(ge=0, le=MAX_WAIT_MS)] = 0


class WorkflowPronunciationApply(StrictModel):
    action: Literal['workflow_pronunciation_apply']
    workflow_id: str
    expected_revision: int
    rationale: str
    changes: list[PronunciationTarget]


DecideRequest = Annotated[
    ScorePatchPlan
    | ScorePatchApply
    | StylePlan
    | LyricsPatchPlan
    | LyricsPatchApply
    | StructurePatchPlan
    | StructurePatchApply
    | PronunciationPatchPlan
    | PronunciationPatchApply
    | DraftsPublish
    | DraftsPlan
    | WorkflowStyleDecide
    | WorkflowApplyReview
    | WorkflowPronunciationApply,
    Field(discriminator='action'),
]


def _status_step(action: str, **arguments):
    return {
        'tool': 'vwa_status',
        'arguments': {'request': {'action': action, **arguments}},
    }



def _with_readback(result: Result, *, purpose: str, instruction: str,
                   steps: list[dict] | None = None,
                   after_terminal: str | None = None,
                   required: bool = True,
                   operation_wait_required: bool = False,
                   approval_required: bool = False) -> Result:
    """Preserve a backend Result while adding an explicit follow-up contract."""
    data = dict(result.data)
    readback = {
        'required': required,
        'purpose': purpose,
        'instruction': instruction,
    }
    if operation_wait_required:
        readback['operation_wait_required'] = True
    if approval_required:
        readback['approval_required'] = True
    existing = data.get('readback')
    if isinstance(existing, dict):
        readback = {**existing, **readback}
    if steps:
        readback['steps'] = steps
    if after_terminal:
        readback['after_terminal'] = after_terminal
    data['readback'] = readback
    if required and isinstance(data.get('flow_control'), dict):
        data['flow_control']['readback_required'] = True
        if approval_required:
            data['flow_control']['approval_required'] = True
    return Result(data=data)

def _plan_readback(result: Result, *, purpose: str, instruction: str) -> Result:
    plan_id = result.data.get('id')
    steps = ([_status_step('plans_get', plan_id=plan_id)]
             if isinstance(plan_id, str) and plan_id else None)
    return _with_readback(
        result,
        purpose=purpose,
        instruction=instruction,
        steps=steps,
    )


def _operation_readback(result: Result, *, purpose: str, instruction: str,
                        candidate_action: str, after_terminal: str) -> Result:
    operation_id = result.data.get('id')
    steps = ([_status_step('operations_get', operation_id=operation_id)]
             if isinstance(operation_id, str) and operation_id else None)
    return _with_readback(
        result,
        purpose=purpose,
        instruction=instruction,
        steps=steps,
        after_terminal=(after_terminal + f' 成功后使用 vwa_evidence action={candidate_action}。'),
    )


def _workflow_readback(result: Result, *, purpose: str, instruction: str,
                       after_terminal: str | None = None,
                       readback_required: bool = True,
                       operation_wait_required: bool | None = None,
                       approval_required: bool = False,
                       include_operation_step: bool = True) -> Result:
    workflow_id = result.data.get('workflow_id') or result.data.get('id')
    operation = result.data.get('operation')
    operation_id = (operation.get('id') if isinstance(operation, dict)
                    else result.data.get('operation_id'))
    steps = []
    if include_operation_step and isinstance(operation_id, str) and operation_id:
        steps.append(_status_step('operations_get', operation_id=operation_id))
    if isinstance(workflow_id, str) and workflow_id:
        steps.append(_status_step('workflow_get', workflow_id=workflow_id))
    return _with_readback(
        result,
        purpose=purpose,
        instruction=instruction,
        steps=steps or None,
        after_terminal=after_terminal,
        required=readback_required,
        operation_wait_required=((isinstance(operation_id, str) and bool(operation_id))
                                  if operation_wait_required is None else operation_wait_required),
        approval_required=approval_required,
    )

_PENDING_REVIEW_STATES = frozenset({'queued', 'running'})
_FAILED_REVIEW_STATES = frozenset({'failed', 'cancelled', 'interrupted'})


def _review_operation(value):
    operation = value.get('operation')
    operation_id = (operation.get('id') if isinstance(operation, dict)
                    else value.get('operation_id'))
    state = operation.get('state') if isinstance(operation, dict) else None
    if state is None and value.get('stage') == 'reviewed':
        # Workflow.get omits the operation object after its result has been
        # consumed into a reviewed candidate. Entering reviewed is proof that
        # the owned review operation succeeded, so do not report it unknown.
        state = 'succeeded'
    return (operation_id if isinstance(operation_id, str) and operation_id else None), state


def _evidence_review_next(workflow_id, revision):
    return {
        'tool': 'vwa_evidence',
        'arguments': {'request': {
            'action': 'workflow_review',
            'workflow_id': workflow_id,
            'limit': 100,
            'offset': 0,
            'detail': 'notes',
        }},
        'reason': 'read_changed_candidate_before_reapproval',
        'then': {
            'tool': 'vwa_decide',
            'arguments': {'request': {
                'action': 'workflow_apply_review',
                'workflow_id': workflow_id,
                'expected_revision': revision,
            }},
            'reason': 'explicit_candidate_reapproval_required',
        },
    }


def _edited_review_readback(result: Result, *, wait_ms: int) -> Result:
    """Keep an edited candidate non-approved and point at the next real step."""
    data = result.data
    workflow_id = data.get('workflow_id') or data.get('id')
    revision = data.get('revision')
    stage = data.get('stage')
    operation_id, operation_state = _review_operation(data)
    candidate_ready = stage in ('review', 'reviewed')
    pending = (stage == 'reviewing'
               and (operation_state in _PENDING_REVIEW_STATES
                    or operation_state is None))
    failed = operation_state in _FAILED_REVIEW_STATES

    if candidate_ready:
        instruction = ('复核操作已完成，修订候选已绑定到工作流。本次编辑绝不等于候选已获认可；'
                       '必须回读其音符、歌词、结构及 revision，必须再次提交明确审核。')
        after_terminal = ('按 vwa_evidence workflow_review 覆盖所需分页；确认候选后，'
                          '以新的 expected_revision 调用 vwa_decide '
                          'workflow_apply_review 明确保留或继续修改。')
        next_step = (_evidence_review_next(workflow_id, revision)
                     if isinstance(workflow_id, str) and workflow_id else None)
    elif pending:
        instruction = ('复核操作仍在运行，编辑候选尚未生成或绑定。等待 operation '
                       '结束并消费结果后；本次编辑绝不等于候选已获认可，必须回读修订 MIDI，必须再次提交明确审核。')
        after_terminal = ('先等待并读取 operation，再回读 workflow；候选绑定后调用 '
                          'vwa_evidence workflow_review 覆盖所需分页，最后以新的 '
                          'expected_revision 再次提交明确审核。')
        budget = wait_ms if wait_ms > 0 else MAX_WAIT_MS
        next_step = None
        if isinstance(operation_id, str) and operation_id:
            next_step = {
                'tool': 'vwa_wait',
                'arguments': {'operation_id': operation_id, 'timeout_ms': budget},
                'reason': 'edited_review_operation_running',
                'after_wait': {
                    'tool': 'vwa_run',
                    'arguments': {'request': {
                        'action': 'workflow_advance',
                        'workflow_id': workflow_id,
                        'expected_revision': revision,
                        'run_until_gate': True,
                        'wait_ms': budget,
                    }},
                    'reason': 'consume_edited_candidate_then_reread',
                },
            }
    elif stage == 'reviewing' and operation_state == 'succeeded':
        instruction = ('复核操作已成功，但工作流尚未消费候选结果。不要等待同一个已完成'
                       'operation；本次编辑绝不等于候选已获认可，先推进绑定候选，并在回读后必须再次提交明确审核。')
        after_terminal = ('按 vwa_run workflow_advance 消费结果；随后以返回 revision '
                          '调用 vwa_evidence workflow_review，再提交明确审核。')
        next_step = {
            'tool': 'vwa_run',
            'arguments': {'request': {
                'action': 'workflow_advance',
                'workflow_id': workflow_id,
                'expected_revision': revision,
                'run_until_gate': True,
                'wait_ms': wait_ms,
            }},
            'reason': 'consume_succeeded_edited_review',
        }
    elif failed:
        instruction = ('复核操作失败或被中断，没有可接受的修订候选。'
                       '本次编辑绝不等于候选已获认可，修复后必须再次提交明确审核。')
        after_terminal = ('读取 operation 错误与当前 workflow；修复来源或计划后重新'
                          '提交明确审核，生产候选后再回读。')
        next_step = (_status_step('operations_get', operation_id=operation_id)
                     if isinstance(operation_id, str) and operation_id else None)
    else:
        instruction = ('无法确认编辑候选已完成绑定。先读取 workflow 和 operation '
                       '状态；未回读候选前不得继续导出。')
        after_terminal = ('确认工作流进入 review/reviewed 后回读音符、歌词和结构，'
                          '再以新 revision 提交明确审核。')
        next_step = (_status_step('workflow_get', workflow_id=workflow_id)
                     if isinstance(workflow_id, str) and workflow_id else None)

    operation_consumed = stage == 'reviewed' and candidate_ready
    readback_result = _workflow_readback(
        result,
        purpose='verify_review_candidate',
        instruction=instruction,
        after_terminal=after_terminal,
        readback_required=True,
        operation_wait_required=pending,
        approval_required=True,
        include_operation_step=not operation_consumed,
    )
    output = dict(readback_result.data)
    output['readback']['operation_wait_required'] = bool(pending)
    output['readback']['operation_state'] = operation_state
    output['readback']['candidate_ready'] = candidate_ready
    if next_step is not None:
        output['next'] = next_step
    if isinstance(output.get('flow_control'), dict):
        output['flow_control']['next_action'] = (
            next_step.get('tool') if next_step is not None else None)
    return Result(data=output)

def _compact_plan(value):
    if isinstance(value, list):
        return [_compact_plan(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key.endswith('note_ids') and isinstance(item, list):
                result[key + '_count'] = len(item)
            else:
                result[key] = _compact_plan(item)
        return result
    return value


def _style_summary(result: Result) -> Result:
    """Match the original style_plan detail=summary view without changing data."""
    data = _compact_plan(result.data)
    plan_id = data.get('id')
    data['details_tool'] = _status_step('plans_get', plan_id=plan_id)
    data['view'] = {'detail': 'summary', 'note_ids_omitted': True}
    return Result(data=data)


def register(mcp, control, workflow, invoke):
    """Register the single typed ``vwa_decide`` standard-profile tool."""

    @mcp.tool(annotations=DECIDE_ANNOTATIONS)
    async def vwa_decide(request: DecideRequest) -> Result:
        """提交调用者已明确作出的审核、修改或风格决定。action 覆盖原
        score/lyrics/structure/pronunciation 的 plan/apply、style_plan、
        drafts_plan/drafts_publish、workflow_style_decide、
        workflow_apply_review 和 workflow_pronunciation_apply。计划不会自动执行，
        审核不会自动认可未读内容；变更后按返回 readback 回读方案、操作候选、
        工作流 revision 或共享草稿 revision。workflow_apply_review 的 dry_run=true 按结构、歌词、音符顺序试算临时候选并销毁，不提交决定或批准；不能与 run_until_gate 或 wait_ms 同用。默认只提交决定；显式 run_until_gate=true 时可在 wait_ms<=60000 内复用流程驱动器，但编辑候选仍在复核结果处停止并要求再次明确审核。SHA、CAS、幂等键与原参数验证仍由 Control/Workflow 执行；本工具不推理、不合成。"""
        if isinstance(request, ScorePatchPlan):
            result = await invoke(
                control.make_plan,
                'score',
                request.artifact_id,
                expected_sha256=request.expected_sha256,
                edits=[edit.model_dump(exclude_none=True) for edit in request.edits],
                move_attached_lyrics=request.move_attached_lyrics,
            )
            return _plan_readback(
                result,
                purpose='review_score_plan',
                instruction='先回读保存的音符 diff；确认仅包含调用者明确要求的音高/时值修改后再显式 apply。',
            )
        if isinstance(request, ScorePatchApply):
            result = await invoke(
                control.start_plan,
                request.plan_id,
                'score',
                request.idempotency_key,
            )
            return _operation_readback(
                result,
                purpose='verify_score_candidate',
                instruction='等待操作结束后回读候选 MIDI 的目标音符；操作写入成功不等于修改已获认可。',
                candidate_action='score_read',
                after_terminal='对成功回执中的候选 artifact_id 执行 vwa_evidence score_read。',
            )
        if isinstance(request, StylePlan):
            result = await invoke(
                control.make_plan,
                'style',
                request.artifact_id,
                proposal=request.proposal.model_dump(),
            )
            if request.detail == 'summary':
                result = _style_summary(result)
            return _plan_readback(
                result,
                purpose='review_style_plan',
                instruction='回读整音符范围、理由和已对齐区间；plan 仅保存调用者给出的风格，不生成候选或声库音频。',
            )
        if isinstance(request, LyricsPatchPlan):
            result = await invoke(
                control.make_plan,
                'lyrics',
                request.artifact_id,
                expected_sha256=request.expected_sha256,
                edits=[edit.model_dump(exclude_none=True) for edit in request.edits],
            )
            return _plan_readback(
                result,
                purpose='review_lyrics_plan',
                instruction=('回读歌词编辑 diff；替换/插入须核对 note_id，move_event 须核对完整源 SHA、'
                             'event_id、目标 note 的同轨唯一 onset，delete_event 须核对事件身份后再显式 apply。'),
            )
        if isinstance(request, LyricsPatchApply):
            result = await invoke(
                control.start_plan,
                request.plan_id,
                'lyrics',
                request.idempotency_key,
            )
            return _operation_readback(
                result,
                purpose='verify_lyrics_candidate',
                instruction=('等待操作结束后回读候选 MIDI 的新 event_id、歌词归属和剩余歧义；'
                             '写入成功不等于语义或时间已获认可。'),
                candidate_action='lyrics_read',
                after_terminal='对成功回执中的候选 artifact_id 执行 vwa_evidence lyrics_read。',
            )
        if isinstance(request, StructurePatchPlan):
            result = await invoke(
                control.make_plan,
                'structure',
                request.artifact_id,
                expected_sha256=request.expected_sha256,
                edits=[edit.model_dump(exclude_none=True) for edit in request.edits],
            )
            return _plan_readback(
                result,
                purpose='review_structure_plan',
                instruction='回读插入/删除和歌词归属策略；歧义必须由调用者重新决定后再显式 apply。',
            )
        if isinstance(request, StructurePatchApply):
            result = await invoke(
                control.start_plan,
                request.plan_id,
                'structure',
                request.idempotency_key,
            )
            return _operation_readback(
                result,
                purpose='verify_structure_candidate',
                instruction='等待操作结束后回读候选 MIDI 的音符、歌词和剩余事件。',
                candidate_action='score_read',
                after_terminal='对成功回执中的候选 artifact_id 执行 vwa_evidence score_read。',
            )
        if isinstance(request, PronunciationPatchPlan):
            result = await invoke(
                control.make_plan,
                'pronunciation',
                request.artifact_id,
                expected_sha256=request.expected_sha256,
                edits=[edit.model_dump() for edit in request.edits],
            )
            return _plan_readback(
                result,
                purpose='review_pronunciation_plan',
                instruction='回读每个 note_ref、原字与目标音素；不自动猜测多音字，也不把写入视为声学正确。',
            )
        if isinstance(request, PronunciationPatchApply):
            result = await invoke(
                control.start_plan,
                request.plan_id,
                'pronunciation',
                request.idempotency_key,
            )
            return _operation_readback(
                result,
                purpose='verify_pronunciation_candidate',
                instruction='等待操作结束后回读候选工程的音素和受保护参数；写入事实不代表声学验收。',
                candidate_action='pronunciation_read',
                after_terminal='对成功回执中的候选 artifact_id 执行 vwa_evidence pronunciation_read，并核对保留的原声参数。',
            )
        if isinstance(request, DraftsPublish):
            result = await invoke(
                control.drafts_publish,
                request.plan_id,
                request.expected_revision,
            )
            return _with_readback(
                result,
                purpose='verify_shared_draft_cas',
                instruction='按方案绑定的 artifact_id 回读 vwa_status drafts_get，确认新 revision；冲突时保留双方并重新读取，禁止盲覆盖 GUI 草稿。',
            )
        if isinstance(request, DraftsPlan):
            result = await invoke(
                control.drafts_plan,
                request.artifact_id,
                request.expected_revision,
                request.brief,
            )
            steps = []
            plan_id = result.data.get('id')
            if isinstance(plan_id, str) and plan_id:
                steps.append(_status_step('plans_get', plan_id=plan_id))
                steps.append(_status_step('drafts_get', artifact_id=request.artifact_id))
            return _with_readback(
                result,
                purpose='review_bound_draft_plan',
                instruction='回读方案与绑定草稿 revision；只有调用者明确选择该方案后才可生成候选或发布。',
                steps=steps or None,
            )
        if isinstance(request, WorkflowStyleDecide):
            from agent_segment_workflow import style_decide
            result = await invoke(
                style_decide,
                workflow,
                request.workflow_id,
                request.expected_revision,
                request.segment_id,
                request.rationale,
                (request.proposal.model_dump()
                 if request.proposal is not None else None),
            )
            data_revision = result.data.get('revision')
            revision = (f"当前返回revision={data_revision}；" if type(data_revision) is int else '')
            return _workflow_readback(
                result,
                purpose='verify_style_decision',
                instruction=revision + '回读工作流与下一待决片段；proposal 为空明确表示保留原表现，不自动进入风格生成。',
                after_terminal='继续按工作流 next 读取证据，不自动认可未读片段。',
            )
        if isinstance(request, WorkflowApplyReview):
            if request.dry_run and (request.run_until_gate or request.wait_ms):
                def reject_dry_run_drive():
                    raise ControlError('invalid_options', 'dry_run 不提交审核，不能驱动工作流或等待操作。')
                return await invoke(reject_dry_run_drive)
            if any(edit.operation in {'move_event', 'delete_event'}
                   for edit in (request.lyric_edits or [])):
                def reject_workflow_event_edit():
                    raise ControlError(
                        'workflow_lyrics_event_edit_unsupported',
                        '本轮暂不支持 workflow_apply_review 内的 move_event/delete_event：'
                        '工作流分段授权与结构修改重映射目前只理解 note_id，候选事件身份可能失效。'
                        '请先通过独立 lyrics_patch_plan/apply 完成事件修补并回读候选，再绑定或创建新的工作流。',
                        {
                            'supported_actions': ['lyrics_patch_plan', 'lyrics_patch_apply'],
                            'workflow_wiring_required': [
                                'segment authorization for event track/tick scope',
                                'event identity validation before and after structure edits',
                                'candidate event identity rebinding and reread',
                            ],
                        },
                    )
                return await invoke(reject_workflow_event_edit)
            if request.wait_ms and not request.run_until_gate:
                def reject_invalid_wait():
                    raise ControlError(
                        'invalid_wait',
                        'wait_ms 仅在 run_until_gate=true 时使用；单独等待请调用 vwa_wait。',
                    )
                return await invoke(reject_invalid_wait)
            result = await invoke(
                workflow.apply_review,
                request.workflow_id,
                request.expected_revision,
                request.rationale,
                ([edit.model_dump(exclude_none=True) for edit in request.note_edits]
                 if request.note_edits else []),
                ([edit.model_dump() for edit in request.lyric_edits]
                 if request.lyric_edits else []),
                ([edit.model_dump(exclude_none=True) for edit in request.structure_edits]
                 if request.structure_edits else []),
                request.move_attached_lyrics,
                request.segment_id,
                *([True] if request.dry_run else []),
            )
            if request.dry_run:
                return result
            has_edits = bool(
                request.note_edits or request.lyric_edits or request.structure_edits)
            if request.run_until_gate:
                # Reuse the flow adapter instead of reimplementing waits and gates
                # in this module. The caller's explicit decision was submitted
                # above; this driver may only consume deterministic backend
                # work and must stop at the next readback or report gate.
                from agent_mcp_standard_flow import _drive_workflow
                try:
                    data = await _drive_workflow(
                        workflow,
                        control,
                        invoke,
                        result.data,
                        action='workflow_apply_review',
                        run_until_gate=True,
                        wait_ms=request.wait_ms,
                        decisions={
                            'style_proposal': None,
                            'finish': False,
                            'export_review': None,
                            'style_plan_id': None,
                        },
                    )
                except (ControlError, ValueError, OSError, TypeError, KeyError) as error:
                    def raise_driver_error(error=error):
                        raise error
                    return await invoke(raise_driver_error)
                result = Result(data=data)
                if has_edits:
                    return _edited_review_readback(result, wait_ms=request.wait_ms)
                return result
            if has_edits:
                return _edited_review_readback(result, wait_ms=request.wait_ms)
            return _workflow_readback(
                result,
                purpose='await_review_operation',
                instruction='本次是无修改的明确保留决定，不要求候选内容回读；仍须等待复核 operation 完成。operation 完成不会自动批准未读证据，后续认可仍按工作流门禁读取。',
                after_terminal='等待并读取 operation；未覆盖所需证据分页时不得把此保留决定解释为自动认可。',
                readback_required=False,
                operation_wait_required=True,
            )
        if isinstance(request, WorkflowPronunciationApply):
            result = await invoke(
                workflow.pronunciation_apply,
                request.workflow_id,
                request.expected_revision,
                [row.model_dump() for row in request.changes],
                request.rationale,
            )
            workflow_id = result.data.get('workflow_id') or result.data.get('id')
            operation_id = result.data.get('operation_id') or result.data.get('id')
            steps = []
            if isinstance(operation_id, str) and operation_id:
                steps.append(_status_step('operations_get', operation_id=operation_id))
            if isinstance(workflow_id, str) and workflow_id:
                steps.append(_status_step('workflow_get', workflow_id=workflow_id))
            return _with_readback(
                result,
                purpose='verify_pronunciation_refinement',
                instruction='等待操作完成后回读新工程报告 SHA、音素和原声参数；acoustic_verification=false，不得宣称声学正确。',
                steps=steps or None,
                after_terminal='用新候选 artifact_id 完成 pronunciation_read 与工程回读，再重新提交导出报告审核。',
            )
        raise AssertionError('unreachable decide action')
