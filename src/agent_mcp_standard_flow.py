"""Typed standard-profile tools for workflow start, deterministic runs, and delivery.

The module translates grouped MCP requests into the existing ``Control`` and
``Workflow`` methods.  It does not create plans/operations directly, approve a
review, or replace backend revision, SHA, lifecycle, and idempotency checks.
"""
from __future__ import annotations

from pathlib import Path
import time
from typing import Annotated, Any, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from agent_control import ControlError
from agent_mcp_standard_routing import standardize
from agent_mcp_models import (
    ExportReview,
    PreparationOptions,
    Result,
    StrictModel,
    StyleProposal,
    WorkflowReviewMode,
)
from agent_views import workflow_view


MAX_WAIT_MS = 60_000
_MAX_AUTO_STEPS = 12
_RUNNING_STAGES = frozenset({'preparing', 'reviewing', 'exporting', 'styling', 'refining'})
_FAILED_OPERATION_STATES = frozenset({'failed', 'cancelled', 'interrupted'})
TERMINAL_OPERATION_STATES = frozenset({'succeeded', *_FAILED_OPERATION_STATES})


# The profile groups contain operations that may start model/download work or
# write managed files.  They cannot honestly be advertised as read-only, and an
# exact retry can also be rejected by revision CAS or a differing idempotency
# request.  The delivery tool writes files but is not open-world.
START_ANNOTATIONS = ToolAnnotations(
    title='Start or plan a VWA workflow',
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
RUN_ANNOTATIONS = ToolAnnotations(
    title='Run deterministic workflow steps',
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
DELIVERY_ANNOTATIONS = ToolAnnotations(
    title='Package an accepted workflow',
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


class WorkflowCreate(StrictModel):
    """Create a workflow, optionally importing one local MIDI file first."""

    action: Literal['workflow_create']
    source: str = ''
    options: PreparationOptions | None = None
    brief: str = ''
    reference_lyrics: str = ''
    score_artifact_id: str | None = None
    formats: list[Literal['svp', 'ustx', 'vsqx']] | None = None
    # Workflow.create persists this intent together with the workflow record.
    # Actual source identities and track mappings are revalidated at export.
    export_options: dict[str, Any] | None = None
    idempotency_key: str | None = None
    preparation_job_id: str | None = None
    review_mode: WorkflowReviewMode = 'full'
    # Convenience source.  The path is still checked against Control's allowed
    # roots by import_asset and is imported exactly once for this call.
    midi_path: Annotated[str, Field(min_length=1)] | None = None
    # Starting preparation can download or run models.  This stays false unless
    # the caller explicitly asks to enter deterministic stages.
    run_until_gate: bool = False
    wait_ms: Annotated[int, Field(ge=0, le=MAX_WAIT_MS)] = 0


class PreparationPlan(StrictModel):
    action: Literal['preparation_plan']
    source: str
    options: PreparationOptions | None = None


class PreparationCreate(StrictModel):
    action: Literal['preparation_create']
    plan_id: str


StartRequest = Annotated[
    WorkflowCreate | PreparationPlan | PreparationCreate,
    Field(discriminator='action'),
]


class WorkflowAdvance(StrictModel):
    """Advance the durable workflow and optionally consume deterministic steps."""

    action: Literal['workflow_advance']
    workflow_id: str
    expected_revision: int
    style_proposal: StyleProposal | None = None
    retry: bool = False
    finish: bool = False
    export_review: ExportReview | None = None
    style_plan_id: str | None = None
    # Kept as an open local option mapping because the existing Control layer
    # resolves public artifact/job bindings and applies the export whitelist.
    export_options: dict[str, Any] | None = None
    # Explicitly enables deterministic preparation/export/style continuation.
    run_until_gate: bool = False
    wait_ms: Annotated[int, Field(ge=0, le=MAX_WAIT_MS)] = 0


class PreparationStart(StrictModel):
    action: Literal['preparation_start']
    job_id: str
    idempotency_key: str
    until: Literal['metadata', 'download', 'audio', 'separation', 'midi'] | None = None


class TuningStart(StrictModel):
    action: Literal['tuning_start']
    plan_id: str
    idempotency_key: str


class ExportsPlan(StrictModel):
    action: Literal['exports_plan']
    artifact_id: str
    formats: list[Literal['svp', 'ustx', 'vsqx']] | None = None
    # Backend Control resolves IDs and validates the public export option set.
    options: dict[str, Any] | None = None


class ExportsStart(StrictModel):
    action: Literal['exports_start']
    plan_id: str
    idempotency_key: str


class ArtifactsInspect(StrictModel):
    # The ordinary read is available through vwa_status.  This branch is the
    # conditional-write route for an actual hash verification operation.
    action: Literal['artifacts_inspect']
    artifact_id: str
    verify: bool = True
    wait_ms: Annotated[int, Field(ge=0, le=MAX_WAIT_MS)] = 0


class ReferenceAudioRead(StrictModel):
    # The ordinary WAV/PCM read is available through vwa_evidence.  This branch
    # represents creation of a playable excerpt operation.
    action: Literal['reference_audio_read']
    artifact_id: str
    start: float | None = None
    end: float | None = None
    make_excerpt: bool = True


RunRequest = Annotated[
    WorkflowAdvance
    | PreparationStart
    | TuningStart
    | ExportsPlan
    | ExportsStart
    | ArtifactsInspect
    | ReferenceAudioRead,
    Field(discriminator='action'),
]


class WorkflowDelivery(StrictModel):
    action: Literal['workflow_delivery']
    workflow_id: str
    expected_revision: int
    # Optional in the compatibility route.  Supplying it explicitly accepts
    # exactly the current report (and its listed losses) before packaging.
    export_review: ExportReview | None = None
    destination: str | None = None
    name: str | None = None


DeliveryRequest = Annotated[WorkflowDelivery, Field(discriminator='action')]


def _options(value: PreparationOptions | None):
    return value.model_dump(exclude_unset=True, exclude_none=True) if value is not None else {}


def _raise_control_error(code, message):
    raise ControlError(code, message)


async def _reject_via_invoke(invoke, code, message):
    """Preserve stable error codes through the shared SDK error boundary."""

    await invoke(_raise_control_error, code, message)
    raise AssertionError('control error wrapper unexpectedly returned')


def _create_workflow(control, workflow, request):
    """Import a local MIDI once, then delegate all validation to Workflow.create."""

    source = request.source
    score_artifact_id = request.score_artifact_id
    preparation_job_id = request.preparation_job_id
    if request.midi_path is not None:
        if source or score_artifact_id or preparation_job_id:
            raise ControlError(
                'invalid_source',
                '请选择歌曲来源、已导入MIDI或已有受管准备任务之一。',
            )
        suffix = Path(request.midi_path).suffix.lower()
        if suffix not in ('.mid', '.midi'):
            raise ControlError('unsupported_source', '已准备输入需为 MIDI 工程。')
        artifact = control.import_asset(request.midi_path)
        score_artifact_id = artifact['id']
        source = ''
    arguments = (
        source,
        _options(request.options),
        request.brief,
        request.reference_lyrics,
        score_artifact_id,
        request.formats,
        request.idempotency_key,
        preparation_job_id,
        request.review_mode,
    )
    if request.export_options is None:
        return workflow.create(*arguments)
    return workflow.create(*arguments, initial_export_options=request.export_options)


async def _workflow_get(workflow, invoke, workflow_id, *, full=False):
    result = await invoke(
        workflow.get,
        workflow_id,
        bool(full),
        'full' if full else None,
        False,
        False,
    )
    return result.data


async def _advance(
    workflow,
    invoke,
    workflow_id,
    revision,
    *,
    style_proposal=None,
    retry=False,
    finish=False,
    export_review=None,
    style_plan_id=None,
    export_options=None,
):
    result = await invoke(
        workflow.advance,
        workflow_id,
        revision,
        style_proposal,
        retry,
        finish,
        export_review,
        style_plan_id,
        export_options,
    )
    return result.data


def _operation(value):
    operation = value.get('operation') if isinstance(value, dict) else None
    return operation if isinstance(operation, dict) else {}


def _operation_id(value):
    operation = _operation(value)
    identifier = operation.get('id')
    return identifier if isinstance(identifier, str) and identifier else value.get('operation_id')


def _remaining_ms(deadline):
    return max(0, min(MAX_WAIT_MS, int((deadline - time.monotonic()) * 1000)))


def _review_readback(value):
    """Describe why an edited review cannot be silently exported."""

    receipt = value.get('review_receipt') if isinstance(value, dict) else None
    if not isinstance(receipt, dict):
        receipt = {}
    receipts = receipt.get('receipts')
    segment_completion = receipt.get('segment_completion')
    last_segment = value.get('last_segment_result') if isinstance(value, dict) else None
    changed_receipts = len(receipts) if isinstance(receipts, list) else 0
    artifact_changed = bool(
        receipt.get('artifact_id')
        and receipt.get('original_artifact_id')
        and receipt['artifact_id'] != receipt['original_artifact_id']
    )
    segment_changed = bool(
        isinstance(segment_completion, dict) and segment_completion.get('edited') is True
    )
    segment_reread = bool(
        isinstance(last_segment, dict) and last_segment.get('requires_reread') is True
    )
    required = changed_receipts > 0 or artifact_changed or segment_changed or segment_reread
    return {
        'required': required,
        'reason': 'review_changes_require_reread' if required else None,
        'artifact_id': receipt.get('artifact_id'),
        'source_sha256': receipt.get('source_sha256'),
        'original_artifact_id': receipt.get('original_artifact_id'),
        'changed_receipts': changed_receipts,
        'artifact_changed': artifact_changed,
        'segment_changed': segment_changed or segment_reread,
    }


def _review_is_clean(value):
    receipt = value.get('review_receipt') if isinstance(value, dict) else None
    if not isinstance(receipt, dict) or receipt.get('caller_reviewed') is not True:
        return False
    return not _review_readback(value)['required']


def _required_readback(value, reason='review_readback_required'):
    readback = _review_readback(value)
    readback['required'] = True
    readback['reason'] = readback.get('reason') or reason
    return readback


async def _reviewed_readback_guard(workflow, invoke, current):
    if not isinstance(current, dict) or current.get('stage') != 'reviewed':
        return None
    full = await _workflow_get(workflow, invoke, current.get('id'), full=True)
    if _review_is_clean(full):
        return None
    return full, _required_readback(full)


def _export_accepted(value):
    summary = value.get('export_summary') or {}
    decision = value.get('export_review') or {}
    return bool(
        isinstance(summary, dict)
        and isinstance(decision, dict)
        and summary.get('report_sha256')
        and decision.get('decision') == 'accept'
        and decision.get('report_sha256') == summary.get('report_sha256')
    )


def _export_decision_matches(current, requested):
    if not isinstance(current, dict) or not isinstance(requested, dict):
        return False
    if any(current.get(key) != requested.get(key)
           for key in ('report_sha256', 'decision')):
        return False
    current_reason = current.get('rationale')
    requested_reason = requested.get('rationale')
    if (not isinstance(current_reason, str) or not isinstance(requested_reason, str)
            or current_reason != requested_reason.strip()):
        return False
    current_codes = current.get('accepted_loss_codes')
    requested_codes = requested.get('accepted_loss_codes')
    return bool(
        isinstance(current_codes, list)
        and isinstance(requested_codes, list)
        and all(isinstance(code, str) and code for code in current_codes + requested_codes)
        and sorted(current_codes) == sorted(requested_codes)
    )


def _step(value, action, *, operation_state=None):
    return {
        'action': action,
        'stage': value.get('stage'),
        'revision': value.get('revision'),
        'operation_id': _operation_id(value),
        'operation_state': operation_state or _operation(value).get('state'),
    }


def _wait_next(workflow_id, revision, operation_id, remaining_ms):
    wait_ms = remaining_ms if 0 < remaining_ms <= MAX_WAIT_MS else MAX_WAIT_MS
    return {
        'tool': 'vwa_wait',
        'arguments': {'operation_id': operation_id, 'timeout_ms': wait_ms},
        'after_wait': {
            'tool': 'vwa_run',
            'arguments': {
                'request': {
                    'action': 'workflow_advance',
                    'workflow_id': workflow_id,
                    'expected_revision': revision,
                    'run_until_gate': True,
                    'wait_ms': wait_ms,
                }
            },
        },
        'reason': 'owned_operation_running',
    }


def _present(value, *, action, run_until_gate, wait_ms, steps, stopped_reason,
             next_step=None, readback=None):
    data = standardize(workflow_view(value))
    data['flow_control'] = {
        'requested_action': action,
        'run_until_gate': bool(run_until_gate),
        'wait_budget_ms': int(wait_ms),
        'wait_deadline_is_per_call': True,
        'steps': steps,
        'stopped_reason': stopped_reason,
    }
    if readback is not None:
        data['readback'] = readback
        data['flow_control']['readback_required'] = bool(readback.get('required'))
    if next_step is not None:
        data['next'] = next_step
    return data


def _run_next(workflow_id, revision, *, run_until_gate=True, wait_ms=MAX_WAIT_MS):
    return {
        'tool': 'vwa_run',
        'arguments': {
            'request': {
                'action': 'workflow_advance',
                'workflow_id': workflow_id,
                'expected_revision': revision,
                'run_until_gate': run_until_gate,
                'wait_ms': min(max(int(wait_ms), 0), MAX_WAIT_MS),
            }
        },
    }


def _evidence_next(workflow_id, revision):
    return {
        'tool': 'vwa_evidence',
        'arguments': {
            'request': {
                'action': 'workflow_review',
                'workflow_id': workflow_id,
                'limit': 100,
                'offset': 0,
                'detail': 'notes',
            }
        },
        'reason': 'read_and_review_changed_artifact_before_export',
        'then': {
            'tool': 'vwa_decide',
            'arguments': {
                'request': {
                    'action': 'workflow_apply_review',
                    'workflow_id': workflow_id,
                    'expected_revision': revision,
                }
            },
            'reason': 'reread_complete_submit_explicit_keep_or_further_edits',
        },
    }


async def _drive_workflow(
    workflow,
    control,
    invoke,
    current,
    *,
    action,
    run_until_gate,
    wait_ms,
    decisions,
    initial_export_options=None,
):
    """Consume only deterministic stages and stop at every decision boundary."""

    workflow_id = current.get('id')
    if not isinstance(workflow_id, str) or not workflow_id:
        return await _reject_via_invoke(
            invoke, 'invalid_workflow_result', '工作流回执缺少身份。'
        )
    deadline = time.monotonic() + wait_ms / 1000
    steps = []
    first_step = True

    for _ in range(_MAX_AUTO_STEPS):
        stage = current.get('stage')
        revision = current.get('revision')

        if stage == 'review':
            readback = _review_readback(current)
            next_step = current.get('next')
            if not isinstance(next_step, dict):
                next_step = _evidence_next(workflow_id, revision)
            return _present(
                current,
                action=action,
                run_until_gate=run_until_gate,
                wait_ms=wait_ms,
                steps=steps,
                stopped_reason='review_required',
                next_step=next_step,
                readback=readback if readback.get('required') else None,
            )

        if stage == 'reviewed':
            full = await _workflow_get(workflow, invoke, workflow_id, full=True)
            readback = _review_readback(full)
            if not _review_is_clean(full):
                return _present(
                    full,
                    action=action,
                    run_until_gate=run_until_gate,
                    wait_ms=wait_ms,
                    steps=steps,
                    stopped_reason='review_readback_required',
                    next_step=_evidence_next(workflow_id, full.get('revision')),
                    readback=readback,
                )
            operation = _operation(current)
            if operation.get('state') in _FAILED_OPERATION_STATES:
                return _present(
                    full,
                    action=action,
                    run_until_gate=run_until_gate,
                    wait_ms=wait_ms,
                    steps=steps,
                    stopped_reason='operation_failed',
                )
            current = await _advance(
                workflow,
                invoke,
                workflow_id,
                revision,
                style_proposal=decisions['style_proposal'],
                retry=False,
                finish=decisions['finish'],
                export_review=decisions['export_review'],
                style_plan_id=decisions['style_plan_id'],
                export_options=None,
            )
            steps.append(_step(current, 'workflow_advance'))
            first_step = False
            continue

        if stage == 'project_ready':
            accepted = _export_accepted(current)
            if not accepted:
                if decisions['export_review'] is None:
                    report_next = _run_next(
                        workflow_id,
                        revision,
                        run_until_gate=True,
                        wait_ms=MAX_WAIT_MS,
                    )
                    summary = current.get('export_summary') or {}
                    report_next.update({
                        'reason': 'review_export_report',
                        'report_sha256': summary.get('report_sha256'),
                        'required_input': {
                            'export_review': {
                                'report_sha256': summary.get('report_sha256'),
                                'decision': ['accept', 'needs_revision'],
                                'rationale': '核对导出音符、歌词、实际时间与逐项损失后填写。',
                                'accepted_loss_codes': summary.get('required_loss_codes', []),
                            }
                        },
                    })
                    return _present(
                        current,
                        action=action,
                        run_until_gate=run_until_gate,
                        wait_ms=wait_ms,
                        steps=steps,
                        stopped_reason='export_review_required',
                        next_step=report_next,
                    )
            elif (
                (current.get('request') or {}).get('brief')
                and decisions['style_proposal'] is None
                and decisions['style_plan_id'] is None
                and not decisions['finish']
            ):
                return _present(
                    current,
                    action=action,
                    run_until_gate=run_until_gate,
                    wait_ms=wait_ms,
                    steps=steps,
                    stopped_reason='style_decision_required',
                )
            current = await _advance(
                workflow,
                invoke,
                workflow_id,
                revision,
                style_proposal=decisions['style_proposal'],
                retry=False,
                finish=decisions['finish'],
                export_review=decisions['export_review'],
                style_plan_id=decisions['style_plan_id'],
                export_options=None,
            )
            steps.append(_step(current, 'workflow_advance'))
            first_step = False
            continue

        if stage == 'ready':
            return _present(
                current,
                action=action,
                run_until_gate=run_until_gate,
                wait_ms=wait_ms,
                steps=steps,
                stopped_reason='completed',
            )

        if stage in _RUNNING_STAGES:
            operation = _operation(current)
            operation_id = _operation_id(current)
            state = operation.get('state')
            if not operation_id:
                return await _reject_via_invoke(
                    invoke,
                    'missing_operation_id',
                    '运行中的工作流缺少操作身份。',
                )
            if state in ('queued', 'running'):
                remaining = _remaining_ms(deadline)
                if remaining <= 0:
                    return _present(
                        current,
                        action=action,
                        run_until_gate=run_until_gate,
                        wait_ms=wait_ms,
                        steps=steps,
                        stopped_reason='wait_timeout',
                        next_step=_wait_next(workflow_id, revision, operation_id, remaining),
                    )
                waited_result = await invoke(
                    control.operation_wait,
                    operation_id,
                    remaining,
                    None,
                    'terminal',
                    'summary',
                )
                waited = waited_result.data
                state = waited.get('state')
                steps.append({
                    'action': 'operation_wait',
                    'operation_id': operation_id,
                    'operation_state': state,
                    'timed_out': bool(waited.get('timed_out')),
                })
                if state not in TERMINAL_OPERATION_STATES or waited.get('timed_out'):
                    current = await _workflow_get(workflow, invoke, workflow_id)
                    return _present(
                        current,
                        action=action,
                        run_until_gate=run_until_gate,
                        wait_ms=wait_ms,
                        steps=steps,
                        stopped_reason='wait_timeout',
                        next_step=_wait_next(
                            workflow_id,
                            current.get('revision'),
                            operation_id,
                            _remaining_ms(deadline),
                        ),
                    )
            if state in _FAILED_OPERATION_STATES:
                current = await _workflow_get(workflow, invoke, workflow_id)
                return _present(
                    current,
                    action=action,
                    run_until_gate=run_until_gate,
                    wait_ms=wait_ms,
                    steps=steps,
                    stopped_reason='operation_failed',
                )
            if state != 'succeeded':
                current = await _workflow_get(workflow, invoke, workflow_id)
                return _present(
                    current,
                    action=action,
                    run_until_gate=run_until_gate,
                    wait_ms=wait_ms,
                    steps=steps,
                    stopped_reason='operation_not_terminal',
                    next_step=_wait_next(
                        workflow_id,
                        current.get('revision'),
                        operation_id,
                        _remaining_ms(deadline),
                    ),
                )
            if current.get('unusable_operation_id') == operation_id:
                return _present(
                    current,
                    action=action,
                    run_until_gate=run_until_gate,
                    wait_ms=wait_ms,
                    steps=steps,
                    stopped_reason='operation_unusable',
                )
            current = await _advance(
                workflow,
                invoke,
                workflow_id,
                revision,
                style_proposal=decisions['style_proposal'],
                retry=False,
                finish=decisions['finish'],
                export_review=decisions['export_review'],
                style_plan_id=decisions['style_plan_id'],
                export_options=None,
            )
            steps.append(_step(current, 'workflow_advance'))
            first_step = False
            continue

        if stage == 'created':
            current = await _advance(
                workflow,
                invoke,
                workflow_id,
                revision,
                style_proposal=decisions['style_proposal'],
                retry=False,
                finish=decisions['finish'],
                export_review=decisions['export_review'],
                style_plan_id=decisions['style_plan_id'],
                export_options=initial_export_options if first_step else None,
            )
            steps.append(_step(current, 'workflow_advance'))
            first_step = False
            continue

        return _present(
            current,
            action=action,
            run_until_gate=run_until_gate,
            wait_ms=wait_ms,
            steps=steps,
            stopped_reason='unsupported_stage',
        )

    return _present(
        current,
        action=action,
        run_until_gate=run_until_gate,
        wait_ms=wait_ms,
        steps=steps,
        stopped_reason='step_limit',
    )


def register(mcp, control, workflow, invoke):
    """Register ``vwa_start``, ``vwa_run``, and ``vwa_delivery``."""

    @mcp.tool(annotations=START_ANNOTATIONS)
    async def vwa_start(request: StartRequest) -> Result:
        """规划、创建或开始工作流。action: workflow_create, preparation_plan,
        preparation_create。workflow_create 可用一个 midi_path 导入本地 MIDI 后
        创建；用 idempotency_key 重试时，来源、MIDI身份、options、格式和审核模式
        必须完全一致，否则保留原工作流并返回 idempotency_conflict。
        run_until_gate=true 才启动已明确的确定性阶段，最多等待 wait_ms（<=60000）；
        到达音符/歌词审核、导出报告或风格判断点即停止。export_options 与创建
        请求原子保存；实际导出时重新校验 F0/伴奏来源和音轨映射。"""

        if isinstance(request, PreparationPlan):
            return await invoke(
                control.preparation_plan,
                request.source,
                _options(request.options),
            )
        if isinstance(request, PreparationCreate):
            return await invoke(control.preparation_create, request.plan_id)
        if not isinstance(request, WorkflowCreate):
            raise AssertionError('unreachable start action')

        created = await invoke(_create_workflow, control, workflow, request)
        current = created.data
        if not request.run_until_gate:
            return Result(data=_present(
                current,
                action='workflow_create',
                run_until_gate=False,
                wait_ms=request.wait_ms,
                steps=[],
                stopped_reason='created',
            ))
        decisions = {
            'style_proposal': None,
            'retry': False,
            'finish': False,
            'export_review': None,
            'style_plan_id': None,
        }
        data = await _drive_workflow(
            workflow,
            control,
            invoke,
            current,
            action='workflow_create',
            run_until_gate=True,
            wait_ms=request.wait_ms,
            decisions=decisions,
            initial_export_options=None,
        )
        return Result(data=data)

    @mcp.tool(annotations=RUN_ANNOTATIONS)
    async def vwa_run(request: RunRequest) -> Result:
        """执行确定性步骤。action: workflow_advance, preparation_start,
        tuning_start, exports_plan, exports_start, artifacts_inspect(verify=true),
        reference_audio_read(make_excerpt=true)。

        workflow_advance 原样保留 revision、style/export 审核输入与导出
        export_options（包含 F0/伴奏 artifact/job 绑定）。run_until_gate=true 时
        仅在明确预算内等待并消费确定阶段；审核本身必须由调用方先完成。无修改
        审核成功后自动启动导出；有 note/lyric/structure 修改时消费操作后停在
        回读点。失败、超时、旧revision及操作不可用均显式返回，不自动重试。"""

        if isinstance(request, PreparationStart):
            return await invoke(
                control.preparation_start,
                request.job_id,
                request.until,
                request.idempotency_key,
            )
        if isinstance(request, TuningStart):
            return await invoke(
                control.start_plan,
                request.plan_id,
                'style',
                request.idempotency_key,
            )
        if isinstance(request, ExportsPlan):
            return await invoke(
                control.make_plan,
                'export',
                request.artifact_id,
                formats=request.formats,
                options=request.options,
            )
        if isinstance(request, ExportsStart):
            return await invoke(
                control.start_plan,
                request.plan_id,
                'export',
                request.idempotency_key,
            )
        if isinstance(request, ArtifactsInspect):
            return await invoke(
                control.inspect_artifact,
                request.artifact_id,
                request.verify,
                request.wait_ms,
            )
        if isinstance(request, ReferenceAudioRead):
            return await invoke(
                control.reference_audio_read,
                request.artifact_id,
                request.start,
                request.end,
                request.make_excerpt,
            )
        if not isinstance(request, WorkflowAdvance):
            raise AssertionError('unreachable run action')

        # This guard applies to the ordinary one-step action as well.  Seeing a
        # reviewed revision is not proof that the caller reread an edited
        # candidate.  Export may start only from a clean, explicit receipt.
        preflight = await _workflow_get(
            workflow, invoke, request.workflow_id, full=True
        )
        if (
            preflight.get('revision') == request.expected_revision
            and preflight.get('stage') == 'reviewed'
            and not _review_is_clean(preflight)
        ):
            readback = _review_readback(preflight)
            readback['required'] = True
            readback['reason'] = readback.get('reason') or 'review_readback_required'
            return Result(data=_present(
                preflight,
                action='workflow_advance',
                run_until_gate=request.run_until_gate,
                wait_ms=request.wait_ms,
                steps=[],
                stopped_reason='review_readback_required',
                next_step=_evidence_next(
                    request.workflow_id, preflight.get('revision')
                ),
                readback=readback,
            ))
        decisions = {
            'style_proposal': (
                request.style_proposal.model_dump()
                if request.style_proposal is not None else None
            ),
            'finish': request.finish,
            'export_review': (
                request.export_review.model_dump()
                if request.export_review is not None else None
            ),
            'style_plan_id': request.style_plan_id,
        }
        current = await _advance(
            workflow,
            invoke,
            request.workflow_id,
            request.expected_revision,
            style_proposal=decisions['style_proposal'],
            retry=request.retry,
            finish=request.finish,
            export_review=decisions['export_review'],
            style_plan_id=request.style_plan_id,
            export_options=request.export_options,
        )
        steps = [_step(current, 'workflow_advance')]
        guarded = await _reviewed_readback_guard(workflow, invoke, current)
        if guarded is not None:
            full, readback = guarded
            return Result(data=_present(
                full,
                action='workflow_advance',
                run_until_gate=request.run_until_gate,
                wait_ms=request.wait_ms,
                steps=steps,
                stopped_reason='review_readback_required',
                next_step=_evidence_next(request.workflow_id, full.get('revision')),
                readback=readback,
            ))
        if not request.run_until_gate:
            if request.wait_ms > 0:
                operation_id = _operation_id(current)
                operation = _operation(current)
                if operation_id and operation.get('state') in ('queued', 'running'):
                    waited_result = await invoke(
                        control.operation_wait,
                        operation_id,
                        request.wait_ms,
                        None,
                        'terminal',
                        'summary',
                    )
                    waited = waited_result.data
                    steps.append({
                        'action': 'operation_wait',
                        'operation_id': operation_id,
                        'operation_state': waited.get('state'),
                        'timed_out': bool(waited.get('timed_out')),
                    })
                    current = await _workflow_get(workflow, invoke, request.workflow_id)
            return Result(data=_present(
                current,
                action='workflow_advance',
                run_until_gate=False,
                wait_ms=request.wait_ms,
                steps=steps,
                stopped_reason='single_advance',
            ))
        data = await _drive_workflow(
            workflow,
            control,
            invoke,
            current,
            action='workflow_advance',
            run_until_gate=True,
            wait_ms=request.wait_ms,
            decisions=decisions,
            initial_export_options=request.export_options,
        )
        # The initial revision is used above only for the duplicate-edit guard.
        data.setdefault('flow_control', {})['initial_expected_revision'] = request.expected_revision
        return Result(data=data)

    @mcp.tool(annotations=DELIVERY_ANNOTATIONS)
    async def vwa_delivery(request: DeliveryRequest) -> Result:
        """为已就绪且报告已接受的工作流生成可移交付包。action:
        workflow_delivery。要求准确 revision；可同时提交针对当前报告 SHA 和
        逐项损失的显式 export_review=accept，然后按新 revision 交付。业务层
        仍复核源/候选 SHA、全部损失、阻断项、路径和文件；本工具不隐式接受
        未提供或未列出的损失，不运行声库合成或平台发布。"""

        if not isinstance(request, WorkflowDelivery):
            raise AssertionError('unreachable delivery action')
        if request.export_review is None:
            # Preserve the legacy delivery call exactly for existing clients and
            # workflows that already carry an accepted review.
            return await invoke(
                workflow.delivery,
                request.workflow_id,
                request.expected_revision,
                request.destination,
                request.name,
            )

        # The business layer still performs the authoritative source/candidate
        # SHA, report-loss, lifecycle, path and package checks.  This preflight
        # keeps an explicit review in the same typed request while preserving
        # exact revision CAS for both the acceptance and delivery writes.
        preflight = await _workflow_get(
            workflow, invoke, request.workflow_id, full=True
        )
        if preflight.get('revision') != request.expected_revision:
            return await _reject_via_invoke(
                invoke,
                'workflow_conflict',
                '工作流版本已改变，请重新读取当前交付与报告状态。',
            )

        review = (
            request.export_review.model_dump()
            if request.export_review is not None else None
        )
        if review is not None:
            summary = preflight.get('export_summary')
            if not isinstance(summary, dict):
                return await _reject_via_invoke(
                    invoke,
                    'delivery_export_review_required',
                    '工作流尚未生成可认可的导出监督报告。',
                )
            current = preflight.get('export_review')
            same_report_decision = bool(
                isinstance(current, dict)
                and current.get('report_sha256') == summary.get('report_sha256')
            )
            matching_retry = bool(
                same_report_decision and _export_decision_matches(current, review)
            )
            if same_report_decision and not matching_retry:
                return await _reject_via_invoke(
                    invoke,
                    'export_review_conflict',
                    '当前报告已有不同的明确决定；请用完全相同的报告SHA、损失和理由重试，'
                    '或先重新读取当前状态。',
                )

        # ``Workflow.delivery`` requires fully accepted evidence.  Only when the
        # caller explicitly supplied the decision do we advance the existing
        # workflow API with an exact revision and the current report SHA.  Its
        # validator rejects stale reports, unacknowledged losses and blockers.
        accepted_revision = request.expected_revision
        working = preflight
        if review is not None:
            current_review = preflight.get('export_review')
            already_accepted = bool(
                matching_retry
                and isinstance(current_review, dict)
                and current_review.get('decision') == 'accept'
            )
            if request.export_review.decision == 'accept':
                if not already_accepted:
                    working = await _advance(
                        workflow,
                        invoke,
                        request.workflow_id,
                        request.expected_revision,
                        export_review=review,
                    )
                    accepted_revision = working.get('revision')
                if working.get('stage') != 'ready' or not _export_accepted(working):
                    data = _present(
                        working,
                        action='workflow_delivery',
                        run_until_gate=False,
                        wait_ms=0,
                        steps=[_step(working, 'workflow_advance')]
                        if not already_accepted else [],
                        stopped_reason='delivery_workflow_not_ready',
                    )
                    data['acceptance'] = {
                        'explicit': True,
                        'decision': review.get('decision'),
                        'report_sha256': review.get('report_sha256'),
                        'accepted_loss_codes': list(review.get('accepted_loss_codes', [])),
                        'accepted_revision': accepted_revision,
                    }
                    data['delivery'] = {
                        'generated': False,
                        'reason': 'workflow_not_ready_or_style_decision_required',
                    }
                    return Result(data=data)
            else:
                if not matching_retry:
                    working = await _advance(
                        workflow,
                        invoke,
                        request.workflow_id,
                        request.expected_revision,
                        export_review=review,
                    )
                    accepted_revision = working.get('revision')
                data = _present(
                    working,
                    action='workflow_delivery',
                    run_until_gate=False,
                    wait_ms=0,
                    steps=([] if matching_retry else
                           [_step(working, 'workflow_advance')]),
                    stopped_reason='export_revision_requested',
                )
                data['acceptance'] = {
                    'explicit': True,
                    'decision': review.get('decision'),
                    'report_sha256': review.get('report_sha256'),
                    'accepted_loss_codes': list(review.get('accepted_loss_codes', [])),
                    'accepted_revision': accepted_revision,
                }
                data['delivery'] = {
                    'generated': False,
                    'reason': 'export_revision_requested',
                }
                return Result(data=data)

        result = await invoke(
            workflow.delivery,
            request.workflow_id,
            accepted_revision,
            request.destination,
            request.name,
        )
        data = result.data
        if review is not None:
            data = dict(data)
            data['acceptance'] = {
                'explicit': True,
                'decision': review.get('decision'),
                'report_sha256': review.get('report_sha256'),
                'accepted_loss_codes': list(review.get('accepted_loss_codes', [])),
                'accepted_revision': accepted_revision,
            }
        return Result(data=standardize(data))
