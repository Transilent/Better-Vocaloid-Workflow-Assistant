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
    song_id_or_url: Annotated[str, Field(
        min_length=1, max_length=2048,
        description=('按显式请求保存外部参考快照；先核对原唱、版本和时长。'
                     '不会自动绑定到准备或工作流。'),
    )]


class LyricTimelineImport(StrictModel):
    action: Literal['lyric_timeline_import']
    path: Annotated[str, Field(
        min_length=1, max_length=4096,
        description=('导入本地 LRC/YRC/JSON 为只读参考；读取前仍须由调用方核对版本。'
                     '不会自动绑定或改写歌词。'),
    )]


class ReferenceImport(StrictModel):
    action: Literal['reference_import']
    path: str
    kind: Literal['pitch', 'audio']


class WorkflowReferencesSet(StrictModel):
    action: Literal['workflow_references_set']
    workflow_id: str
    expected_revision: int
    rationale: Annotated[str, Field(
        min_length=1, max_length=8000,
        description='说明参考来源、对齐依据或清除理由；确认未证的来源时要明确写出。',
    )]
    pitch_artifact_id: str | None = None
    audio_artifact_id: str | None = None
    track_indices: list[int] | None = None
    time_origin: float = 0.0
    audio_time_origin: float = 0.0
    lyrics_artifact_id: str | None = Field(
        default=None,
        description=('只有调用方已读取参考并明确确认使用、版本和时间偏移时才填写；'
                     '必须来自歌词时间轴 artifact。不提供歌词参考不受强制。'),
    )
    lyrics_time_origin: Annotated[float, Field(
        description='显式时间偏移：MIDI 秒 = 参考秒 + lyrics_time_origin。',
    )] = 0.0
    lyric_version_note: Annotated[str, Field(
        default='', max_length=4000,
        description=('显式绑定歌词参考时填写原唱/翻唱、版本核对和时间偏移依据；'
                     '未绑定歌词时可留空。'),
    )]


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
    source_job_id: Annotated[str, Field(
        min_length=1, max_length=180,
        description='已完成或部分完成的原受管准备任务。',
    )]
    options: PreparationOptions | None = Field(
        default=None,
        description=('可选覆盖准备参数。若原任务未使用正式歌词，可在调用方确认后通过 '
                     'options.lyrics 重跑所需阶段；这是显式恢复，不会自动重对齐。'),
    )
    reuse_stages: list[Literal['metadata', 'download', 'audio', 'separation']] | None = Field(
        default=None,
        description='明确列出复用阶段；未复用阶段可按 options 重跑。',
    )


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
        时间偏移和歌词版本说明，不执行推理或合成。

        歌词先输入：新歌曲若已有并核对正式歌词，优先在准备计划的 options.lyrics
        或工作流创建的 reference_lyrics 中随任务输入，再开始 MIDI。歌词不是必填项；
        没有正式来源时可继续自动识别。搜索、抓取、导入和 lyric_timeline_read 只生成
        参考证据，不会自动绑定、重排或改写歌词。只有调用方明确确认版本和
        时间偏移后，才用 workflow_references_set 显式绑定时间轴。已有准备若
        需要补歌词，可先提出 preparation_reuse_plan 并让调用方决定是否重跑。
        """
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
        不覆盖源任务。返回计划或修复操作身份供后续显式选择。已有准备若未使用
        正式歌词，可显式传入 options.lyrics 并选择复用阶段；该工具只生成计划，
        不会因发现外部歌词而自动重对齐，也不会强制要求歌词。"""
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
