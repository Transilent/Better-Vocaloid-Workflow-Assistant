"""Typed MCP tools for asset input, recovery, and workflow lifecycle.

This module only translates the standard-profile request unions into the
existing Control/Workflow operations. Authorisation, revision checks, SHA
validation, plan binding, and idempotency remain backend responsibilities.
"""
from __future__ import annotations

from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from agent_mcp_models import PreparationOptions, Result, StrictModel, WorkflowReviewMode


# The fetch action reaches NetEase and writes a managed snapshot. References
# replace workflow-bound evidence under CAS, so the whole tool cannot safely be
# advertised as read-only or idempotent.
INPUT_ANNOTATIONS = ToolAnnotations(
    title='Import assets and bind references',
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
# Plans are content-addressed and repair_start consumes a caller-stable key.
# No model, download, or synthesis is started by this tool.
RECOVER_ANNOTATIONS = ToolAnnotations(
    title='Plan preparation recovery',
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
# rebind/close/archive use workflow revision CAS, so replaying a successful
# call normally conflicts. clone itself also has a deterministic retry key.
MANAGE_ANNOTATIONS = ToolAnnotations(
    title='Manage workflow lifecycle',
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


class AssetsImport(StrictModel):
    action: Literal['assets_import']
    path: str


class LyricTimelineFetch(StrictModel):
    action: Literal['lyric_timeline_fetch']
    song_id_or_url: str


class LyricTimelineImport(StrictModel):
    action: Literal['lyric_timeline_import']
    path: str


class ReferenceImport(StrictModel):
    action: Literal['reference_import']
    path: str
    kind: Literal['pitch', 'audio']


class WorkflowReferencesSet(StrictModel):
    action: Literal['workflow_references_set']
    workflow_id: str
    expected_revision: int
    rationale: str
    pitch_artifact_id: str | None = None
    audio_artifact_id: str | None = None
    track_indices: list[int] | None = None
    time_origin: float = 0.0
    audio_time_origin: float = 0.0
    lyrics_artifact_id: str | None = None
    lyrics_time_origin: float = 0.0
    lyric_version_note: str = ''


InputRequest = Annotated[
    AssetsImport
    | LyricTimelineFetch
    | LyricTimelineImport
    | ReferenceImport
    | WorkflowReferencesSet,
    Field(discriminator='action'),
]


class PreparationReusePlan(StrictModel):
    action: Literal['preparation_reuse_plan']
    source_job_id: str
    options: PreparationOptions | None = None
    reuse_stages: list[Literal['metadata', 'download', 'audio', 'separation']] | None = None


class PreparationRepairPlan(StrictModel):
    action: Literal['preparation_repair_plan']
    source_job_id: Annotated[str, Field(min_length=1, max_length=180)]


class PreparationRepairStart(StrictModel):
    action: Literal['preparation_repair_start']
    plan_id: Annotated[str, Field(min_length=1, max_length=180)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]


RecoverRequest = Annotated[
    PreparationReusePlan | PreparationRepairPlan | PreparationRepairStart,
    Field(discriminator='action'),
]


class WorkflowRebindScore(StrictModel):
    action: Literal['workflow_rebind_score']
    workflow_id: str
    expected_revision: int
    score_artifact_id: str
    reason: str


class WorkflowClose(StrictModel):
    action: Literal['workflow_close']
    workflow_id: str
    expected_revision: int
    reason: str


class WorkflowArchive(StrictModel):
    action: Literal['workflow_archive']
    workflow_id: str
    expected_revision: int
    reason: str


class WorkflowClone(StrictModel):
    action: Literal['workflow_clone']
    workflow_id: str
    expected_revision: int
    reason: str
    score_artifact_id: str | None = None
    idempotency_key: str | None = None
    review_mode: WorkflowReviewMode = 'full'


ManageRequest = Annotated[
    WorkflowRebindScore | WorkflowClose | WorkflowArchive | WorkflowClone,
    Field(discriminator='action'),
]


def register(mcp, control, workflow, invoke):
    """Register the three standard-profile tools on an MCP server.

    ``invoke`` is the profile-level async error/result adapter. This module does
    not catch backend exceptions or construct its own operation receipts.
    """

    @mcp.tool(annotations=INPUT_ANNOTATIONS)
    async def vwa_input(request: InputRequest) -> Result:
        """导入/绑定输入。action: assets_import, lyric_timeline_fetch,
        lyric_timeline_import, reference_import, workflow_references_set。
        路径由Control原有授权根校验；fetch联网并写快照；绑定使用原revision、
        时间偏移和歌词版本说明，不执行推理或合成。"""
        if isinstance(request, AssetsImport):
            return await invoke(control.import_asset, request.path)
        if isinstance(request, LyricTimelineFetch):
            return await invoke(control.lyric_timeline_fetch, request.song_id_or_url)
        if isinstance(request, LyricTimelineImport):
            return await invoke(control.lyric_timeline_import, request.path)
        if isinstance(request, ReferenceImport):
            return await invoke(control.import_reference, request.path, request.kind)
        if isinstance(request, WorkflowReferencesSet):
            return await invoke(
                workflow.references_set,
                request.workflow_id,
                request.expected_revision,
                request.pitch_artifact_id,
                request.audio_artifact_id,
                request.track_indices,
                request.time_origin,
                request.audio_time_origin,
                request.rationale,
                request.lyrics_artifact_id,
                request.lyrics_time_origin,
                request.lyric_version_note,
            )
        raise AssertionError('unreachable input action')

    @mcp.tool(annotations=RECOVER_ANNOTATIONS)
    async def vwa_recover(request: RecoverRequest) -> Result:
        """规划或启动受管恢复。action: preparation_reuse_plan,
        preparation_repair_plan, preparation_repair_start。复用核验原任务SHA与
        选项；修复只复核既有声部并生成新候选，不自动降级双声部、不跑模型、
        不覆盖源任务。返回计划或修复操作身份供后续显式选择。"""
        if isinstance(request, PreparationReusePlan):
            options = (request.options.model_dump(exclude_unset=True, exclude_none=True)
                       if request.options is not None else {})
            return await invoke(
                control.preparation_reuse_plan,
                request.source_job_id,
                options,
                request.reuse_stages,
            )
        if isinstance(request, PreparationRepairPlan):
            return await invoke(
                control.preparation_repair_plan,
                request.source_job_id,
            )
        if isinstance(request, PreparationRepairStart):
            return await invoke(
                control.preparation_repair_start,
                request.plan_id,
                request.idempotency_key,
            )
        raise AssertionError('unreachable recovery action')

    @mcp.tool(annotations=MANAGE_ANNOTATIONS)
    async def vwa_manage(request: ManageRequest) -> Result:
        """管理工作流证据与生命周期。action: workflow_rebind_score,
        workflow_close, workflow_archive, workflow_clone。保留旧revision与失败
        证据；运行中操作须先取消并等待；所有变更沿用原revision CAS、幂等键及
        clone默认full整曲复核。"""
        if isinstance(request, WorkflowRebindScore):
            return await invoke(
                workflow.rebind_score,
                request.workflow_id,
                request.expected_revision,
                request.score_artifact_id,
                request.reason,
            )
        if isinstance(request, WorkflowClose):
            return await invoke(
                workflow.close,
                request.workflow_id,
                request.expected_revision,
                request.reason,
            )
        if isinstance(request, WorkflowArchive):
            return await invoke(
                workflow.close,
                request.workflow_id,
                request.expected_revision,
                request.reason,
                True,
            )
        if isinstance(request, WorkflowClone):
            return await invoke(
                workflow.clone,
                request.workflow_id,
                request.expected_revision,
                request.score_artifact_id,
                request.reason,
                request.idempotency_key,
                request.review_mode,
            )
        raise AssertionError('unreachable management action')
