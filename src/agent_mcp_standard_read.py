"""Typed standard-profile tools for status, evidence, waiting, and search.

This module is an MCP adapter only. Existing Control/Workflow methods remain the
authority for roots, IDs, revisions, SHAs, operation receipts, and persistence.
It does not run inference, synthesis, downloads, or workflow continuation.
"""
from __future__ import annotations

from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from agent_control import ControlError
from agent_mcp_models import Result, StrictModel


# Worker invocations create bounded call records. This tool also exposes
# artifacts_list(job_id), which registers artifacts, and operations_get, which
# may mark a lost worker as interrupted. It cannot claim to be read-only.
STATUS_ANNOTATIONS = ToolAnnotations(
    title='Read task and workflow status',
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
# Evidence reads use worker call records. Shared draft reads can also observe
# another process's live state. The tool is conservative about those effects.
EVIDENCE_ANNOTATIONS = ToolAnnotations(
    title='Read bounded review evidence',
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
# Waiting can reconcile a missing worker to interrupted; job snapshots also
# create worker call records. Repeating a wait is not a strict no-op.
WAIT_ANNOTATIONS = ToolAnnotations(
    title='Wait for bounded progress',
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
CANCEL_ANNOTATIONS = ToolAnnotations(
    title='Request cooperative cancellation',
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
# The search reaches NetEase and records the worker invocation. Its result may
# change over time, so it is neither closed-world nor strictly read-only.
SEARCH_ANNOTATIONS = ToolAnnotations(
    title='Search lyric timeline sources',
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)


class SystemCapabilities(StrictModel):
    action: Literal['system_capabilities']


class SystemHealth(StrictModel):
    action: Literal['system_health']


class JobsList(StrictModel):
    action: Literal['jobs_list']
    limit: int = 50
    offset: int = 0


class JobsGet(StrictModel):
    action: Literal['jobs_get']
    job_id: str
    wait_ms: int = 0
    after_cursor: str | None = None


class ArtifactsList(StrictModel):
    action: Literal['artifacts_list']
    job_id: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    kind: str | None = Field(default=None, description='按产物类型精确过滤；任务索引中对应 role。')
    expected_revision: str | None = Field(default=None, description='offset 非零时必须传上一页 listing_revision；列表变化须从头读取。')


class ArtifactsInspect(StrictModel):
    action: Literal['artifacts_inspect']
    artifact_id: str
    verify: bool = False
    wait_ms: int = 0


class OperationsGet(StrictModel):
    action: Literal['operations_get']
    operation_id: str


class PlansGet(StrictModel):
    action: Literal['plans_get']
    plan_id: str


class TuningResult(StrictModel):
    action: Literal['tuning_result']
    operation_id: str


class DraftsGet(StrictModel):
    action: Literal['drafts_get']
    artifact_id: str


class WorkflowGet(StrictModel):
    action: Literal['workflow_get']
    workflow_id: str
    include_details: bool = False
    detail: Literal['summary', 'full'] = 'summary'
    include_note_ids: bool = False
    include_raw_evidence: bool = False


class WorkflowsList(StrictModel):
    action: Literal['workflows_list']
    limit: int = 20
    offset: int = 0
    status: str | None = None
    query: str = ''


StatusRequest = Annotated[
    SystemCapabilities
    | SystemHealth
    | JobsList
    | JobsGet
    | ArtifactsList
    | ArtifactsInspect
    | OperationsGet
    | PlansGet
    | TuningResult
    | DraftsGet
    | WorkflowGet
    | WorkflowsList,
    Field(discriminator='action'),
]


class ScoreRead(StrictModel):
    action: Literal['score_read']
    artifact_id: str


class ScoreReview(StrictModel):
    action: Literal['score_review']
    artifact_id: str


class ContextRead(StrictModel):
    action: Literal['context_read']
    artifact_id: str
    track_indices: list[int] | None = None
    limit: int = 20
    offset: int = 0
    detail: Literal['summary', 'notes', 'full'] = 'notes'
    include_note_ids: bool = False


class LyricsRead(StrictModel):
    action: Literal['lyrics_read']
    artifact_id: str
    language: Literal['zh', 'ja'] = 'zh'


class LyricTimelineRead(StrictModel):
    action: Literal['lyric_timeline_read']
    artifact_id: Annotated[str, Field(
        min_length=1, max_length=80,
        description='由 lyric_timeline_fetch/import 登记的歌词时间轴 artifact。',
    )]
    limit: Annotated[int, Field(ge=1, le=200)] = 100
    offset: Annotated[int, Field(ge=0)] = 0
    include_words: bool = Field(
        default=False,
        description='YRC 等逐词时间轴是否在本页附带 words；默认只返回词元数量。',
    )


class LyricTimelineReview(StrictModel):
    action: Literal['lyric_timeline_review']
    score_artifact_id: str
    timeline_artifact_id: str
    start: float
    end: float
    time_origin: float = 0.0
    track_indices: list[int] | None = None
    limit: int = 20
    offset: int = 0


class LyricsReview(StrictModel):
    action: Literal['lyrics_review']
    artifact_id: str
    reference_text: str | None = None
    language: Literal['zh', 'ja'] = 'zh'


class LyricReconcileLine(StrictModel):
    start: float
    end: float
    text: Annotated[str, Field(min_length=1, max_length=1000)]


class LyricsReconcilePreview(StrictModel):
    action: Literal['lyrics_reconcile_preview']
    artifact_id: str
    expected_sha256: Annotated[str, Field(pattern=r'^[0-9a-fA-F]{64}$')]
    track_index: int = Field(ge=0, description='明确选择一个 MIDI 轨道，避免混合声部。')
    lines: Annotated[list[LyricReconcileLine], Field(min_length=1, max_length=200)]
    language: Literal['zh', 'ja'] = 'zh'


class ReferencePitchReview(StrictModel):
    action: Literal['reference_pitch_review']
    score_artifact_id: str
    pitch_artifact_id: str
    track_indices: list[int] | None = None
    time_origin: float = 0.0
    limit: int = 100
    offset: int = 0


class PitchOffsetReview(StrictModel):
    action: Literal['pitch_offset_review']
    score_artifact_id: str
    pitch_artifact_id: str
    track_index: int = Field(ge=0, description='MIDI 轨道索引，须与参考 F0 所属声部对应。')
    time_origin: float = 0.0
    category: Literal['warning', 'unevaluated', 'ambiguous']
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    expected_revision: str | None = Field(default=None, description='offset 非零时必须使用上一页的 revision。')


class ReferenceAudioRead(StrictModel):
    action: Literal['reference_audio_read']
    artifact_id: str
    start: float | None = None
    end: float | None = None
    make_excerpt: bool = False


class PronunciationRead(StrictModel):
    action: Literal['pronunciation_read']
    artifact_id: str
    limit: int = 100
    offset: int = 0


class WorkflowReview(StrictModel):
    action: Literal['workflow_review']
    workflow_id: str
    limit: int = 20
    offset: int = 0
    detail: Literal['summary', 'notes', 'full'] = 'notes'
    include_note_ids: bool = False
    include_raw_evidence: bool = False
    segment_id: str | None = None


class WorkflowStyleReview(StrictModel):
    action: Literal['workflow_style_review']
    workflow_id: str
    segment_id: str | None = None


EvidenceRequest = Annotated[
    ScoreRead
    | ScoreReview
    | ContextRead
    | LyricsRead
    | LyricTimelineRead
    | LyricTimelineReview
    | LyricsReview
    | LyricsReconcilePreview
    | ReferencePitchReview
    | PitchOffsetReview
    | ReferenceAudioRead
    | PronunciationRead
    | WorkflowReview
    | WorkflowStyleReview,
    Field(discriminator='action'),
]


def register(mcp, control, workflow, invoke):
    """Register the five read/wait/cancel/search tools.

    ``invoke`` is the profile-level async adapter for Result construction and
    uniform ToolError serialization. This module never bypasses Control or
    Workflow and never performs an arbitrary method lookup.
    """

    async def invalid(code, message, remediation=None, details=None):
        def raise_invalid():
            error_details = dict(details or {})
            if remediation is not None:
                error_details['suggested_tool'] = remediation
            error = ControlError(code, message, error_details or None)
            if remediation is not None:
                error.remediation = [remediation]
            raise error

        return await invoke(raise_invalid)

    def write_redirect(action, arguments, message):
        return {
            'tool': 'vwa_run',
            'arguments': {'request': {'action': action, **arguments}},
        }, message

    @mcp.tool(annotations=STATUS_ANNOTATIONS)
    async def vwa_status(request: StatusRequest) -> Result:
        """读取系统、任务、产物、计划、草稿与工作流状态。

        action 为原工具名去掉 vwa_：system_capabilities, system_health,
        jobs_list, jobs_get, artifacts_list, artifacts_inspect, operations_get,
        plans_get, tuning_result, drafts_get, workflow_get, workflows_list。
        artifacts_list 支持 kind 过滤和分页；续页使用 next_request，任务索引的当前页会登记并校验产物；operations_get 和
        workflow 状态查询可能把失联操作标为 interrupted，因此本工具不标纯只读。
        artifacts_inspect(verify=true) 会启动核验操作，须改走 vwa_run。
        """
        if isinstance(request, SystemCapabilities):
            return await invoke(control.capabilities)
        if isinstance(request, SystemHealth):
            return await invoke(control.call_worker, 'health', {})
        if isinstance(request, JobsList):
            return await invoke(control.jobs_list, request.limit, request.offset)
        if isinstance(request, JobsGet):
            return await invoke(
                control.job_get,
                request.job_id,
                request.wait_ms,
                request.after_cursor,
            )
        if isinstance(request, ArtifactsList):
            return await invoke(control.artifacts_list, request.job_id, request.limit, request.offset,
                                request.kind, request.expected_revision)
        if isinstance(request, ArtifactsInspect):
            if request.verify:
                remediation, message = write_redirect(
                    'artifacts_inspect',
                    {
                        'artifact_id': request.artifact_id,
                        'verify': True,
                        'wait_ms': request.wait_ms,
                    },
                    'artifacts_inspect(verify=true) 会启动摘要核验操作。',
                )
                return await invalid('write_action_required', message, remediation)
            return await invoke(
                control.inspect_artifact,
                request.artifact_id,
                False,
                request.wait_ms,
            )
        if isinstance(request, OperationsGet):
            return await invoke(control.operation_get, request.operation_id)
        if isinstance(request, PlansGet):
            return await invoke(control.plan_get, request.plan_id)
        if isinstance(request, TuningResult):
            def tuning_result():
                value = control.operation_get(request.operation_id)
                if value['request']['action'] != 'style_render':
                    raise ControlError('wrong_operation', '该操作不是风格候选生成。')
                return value

            return await invoke(tuning_result)
        if isinstance(request, DraftsGet):
            return await invoke(control.drafts_get, request.artifact_id)
        if isinstance(request, WorkflowGet):
            result = await invoke(
                workflow.get,
                request.workflow_id,
                request.include_details,
                request.detail,
                request.include_note_ids,
                request.include_raw_evidence,
            )
            # A compact reviewed receipt deliberately omits the edit details.
            # Resolve that one safety question inside this status call, while
            # returning the requested compact view to the caller. A concurrent
            # revision change keeps the conservative full-status next step.
            next_step = result.data.get('next') or {}
            if (isinstance(next_step, dict) and result.data.get('stage') == 'reviewed'
                    and next_step.get('reason') == 'read_full_review_receipt_before_advance'):
                full = await invoke(workflow.get, request.workflow_id, True, 'full', False, False)
                if full.data.get('revision') == result.data.get('revision'):
                    data = dict(result.data)
                    data['next'] = full.data.get('next')
                    return Result(data=data)
            return result
        if isinstance(request, WorkflowsList):
            return await invoke(
                workflow.list,
                request.limit,
                request.offset,
                request.status,
                request.query,
            )
        raise AssertionError('unreachable status action')

    @mcp.tool(annotations=EVIDENCE_ANNOTATIONS)
    async def vwa_evidence(request: EvidenceRequest) -> Result:
        """读取 MIDI、歌词、参考与工作流的有界核对证据。

        action 为动作名（沿用原工具名去掉 vwa_；lyric_timeline_read 是新增
        标准只读动作）：score_read, score_review, context_read, lyrics_read,
        lyric_timeline_read, lyric_timeline_review, lyrics_review, lyrics_reconcile_preview,
        reference_pitch_review, pitch_offset_review, reference_audio_read, pronunciation_read,
        workflow_review, workflow_style_review。workflow_style_review 在
        project_ready 读取下一逐段风格上下文，在 styling 返回操作进行中状态，
        ready 后只回读已应用的风格结果。读取经原 Control/Workflow
        分页、SHA 和阶段校验，并保留 worker 调用记录；参考证据不是真值。
        lyric_timeline_read 返回 line_index、起止秒、text、estimated、provider
        和版本核验状态；读取不会绑定工作流、
        自动重排或改写歌词。新歌曲若已有核对后的正式歌词，应优先在准备阶段
        随 options.lyrics 输入；外部搜索、抓取和读取都不是必须步骤。
        context_read 的 presets 与 techniques 来自同一轻量登记表，前者只列新选择
        可见风格，后者是独立唱腔层；应使用返回的 ID、顺序和描述，不能把唱腔 ID
        填进 preset，也不能因 legacy 展示归并而改写待执行的旧 ID。
        lyrics_reconcile_preview 根据已核对歌词行和所选轨道提出候选，按完整源 SHA
        绑定，不自动修改、拆分或确认；缺字及多音节须人工处理。
        reference_audio_read(make_excerpt=true) 会创建摘录操作，须改走 vwa_run。
        """
        if isinstance(request, ScoreRead):
            return await invoke(control.score_read, request.artifact_id)
        if isinstance(request, ScoreReview):
            return await invoke(control.score_read, request.artifact_id, True)
        if isinstance(request, ContextRead):
            return await invoke(
                control.context_read,
                request.artifact_id,
                request.track_indices,
                request.limit,
                request.offset,
                request.detail,
                request.include_note_ids,
            )
        if isinstance(request, LyricsRead):
            return await invoke(
                control.lyrics_read,
                request.artifact_id,
                language=request.language,
            )
        if isinstance(request, LyricTimelineRead):
            return await invoke(
                control.lyric_timeline_read,
                request.artifact_id,
                request.limit,
                request.offset,
                request.include_words,
            )
        if isinstance(request, LyricTimelineReview):
            return await invoke(
                control.lyric_timeline_review,
                request.score_artifact_id,
                request.timeline_artifact_id,
                request.time_origin,
                request.start,
                request.end,
                request.track_indices,
                request.limit,
                request.offset,
            )
        if isinstance(request, LyricsReview):
            return await invoke(
                control.lyrics_read,
                request.artifact_id,
                request.reference_text,
                request.language,
                True,
            )
        if isinstance(request, LyricsReconcilePreview):
            def preview_reconciliation():
                from agent_lyric_reconcile import reconcile_lyrics
                read = control.lyrics_read(request.artifact_id, language=request.language)
                if read['source_sha256'].lower() != request.expected_sha256.lower():
                    raise ControlError('stale_source', 'MIDI SHA 已变化，请重新读取歌词后再预览。')
                preview = reconcile_lyrics(read, [line.model_dump() for line in request.lines],
                                           request.track_index)
                preview['artifact_id'] = request.artifact_id
                preview['next_instruction'] = (
                    '逐行核对文字、时间窗和音符归属；确认后使用 vwa_decide 的 '
                    'lyrics_patch_plan，并传入本预览的 artifact_id、source_sha256 和 edits。'
                    '候选补丁需另行显式 apply 并回读；未覆盖、延音及拆分问题须人工决定。'
                )
                return preview
            return await invoke(preview_reconciliation)
        if isinstance(request, ReferencePitchReview):
            return await invoke(
                control.reference_pitch_review,
                request.score_artifact_id,
                request.pitch_artifact_id,
                request.track_indices,
                request.time_origin,
                request.limit,
                request.offset,
            )
        if isinstance(request, PitchOffsetReview):
            return await invoke(control.pitch_offset_review, request.score_artifact_id,
                                request.pitch_artifact_id, request.track_index,
                                request.time_origin, request.category, request.limit,
                                request.offset, request.expected_revision)
        if isinstance(request, ReferenceAudioRead):
            if request.make_excerpt:
                remediation, message = write_redirect(
                    'reference_audio_read',
                    {
                        'artifact_id': request.artifact_id,
                        'start': request.start,
                        'end': request.end,
                        'make_excerpt': True,
                    },
                    'reference_audio_read(make_excerpt=true) 会创建音频摘录操作。',
                )
                return await invalid('write_action_required', message, remediation)
            return await invoke(
                control.reference_audio_read,
                request.artifact_id,
                request.start,
                request.end,
                False,
            )
        if isinstance(request, PronunciationRead):
            return await invoke(
                control.pronunciation_read,
                request.artifact_id,
                request.limit,
                request.offset,
            )
        if isinstance(request, WorkflowReview):
            return await invoke(
                workflow.review,
                request.workflow_id,
                request.limit,
                request.offset,
                request.detail,
                request.include_note_ids,
                request.include_raw_evidence,
                request.segment_id,
            )
        if isinstance(request, WorkflowStyleReview):
            from agent_segment_workflow import style_review
            return await invoke(
                style_review,
                workflow,
                request.workflow_id,
                request.segment_id,
            )
        raise AssertionError('unreachable evidence action')

    @mcp.tool(annotations=WAIT_ANNOTATIONS)
    async def vwa_wait(operation_id: str | None = None, job_id: str | None = None,
            timeout_ms: Annotated[int, Field(strict=True)] = 30000, after_cursor: str | None = None,
            return_on: Literal['terminal', 'change'] = 'terminal',
            detail: Literal['summary', 'full'] = 'summary',
            wait_ms: Annotated[int, Field(strict=True)] | None = None) -> Result:
        """有界等待本服务操作或已有准备任务，最长60000毫秒。

        operation_id 使用原 operations_wait 参数，timeout_ms 默认30000；
        job_id 使用原 jobs_get 的 wait_ms 语义。为便于统一调用，job_id 未传
        wait_ms 时采用 timeout_ms。两者必须且只能提供一个。超时返回当前状态；
        本工具不会创建或推进 workflow。
        """
        if (operation_id is None) == (job_id is None):
            return await invalid(
                'invalid_wait',
                'operation_id 或 job_id 需且只能提供一个。',
            )
        if operation_id is not None:
            if wait_ms is not None:
                return await invalid(
                    'invalid_wait',
                    'operation_id 使用 timeout_ms；wait_ms 仅用于 job_id。',
                )
            timeout = timeout_ms
        else:
            if return_on != 'terminal' or detail != 'summary':
                return await invalid(
                    'invalid_wait',
                    'job_id 等待使用原任务快照接口，不支持 return_on/detail。',
                )
            timeout = timeout_ms if wait_ms is None else wait_ms
        if type(timeout) is not int or not 0 <= timeout <= 60000:
            return await invalid(
                'invalid_wait',
                '等待时长需为0–60000毫秒；超时返回当前状态。',
            )
        if operation_id is not None:
            return await invoke(
                control.operation_wait,
                operation_id,
                timeout,
                after_cursor,
                return_on,
                detail,
            )
        return await invoke(control.job_get, job_id, timeout, after_cursor)

    @mcp.tool(annotations=CANCEL_ANNOTATIONS)
    async def vwa_cancel(operation_id: str) -> Result:
        """请求协作取消本服务拥有的操作；保留 cancel_requested/cancelled 区分。"""
        return await invoke(control.operation_cancel, operation_id)

    @mcp.tool(annotations=SEARCH_ANNOTATIONS)
    async def vwa_search(query: str,
            limit: Annotated[int, Field(strict=True)] = 10,
            offset: Annotated[int, Field(strict=True)] = 0) -> Result:
        """从固定网易云来源搜索歌曲版本与时长，只返回元数据；需人工核对版本。"""
        return await invoke(control.lyric_timeline_search, query, limit, offset)
