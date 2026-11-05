"""Shared strict request/response models for VWA MCP profiles."""
from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    model_serializer,
    model_validator,
)
from agent_preparation import PREPARATION_BPM_RANGE, PREPARATION_CPU_THREADS_RANGE, PREPARATION_DEVICE_VALUES
from style_catalog import DEFAULT_TECHNIQUE_ID, TECHNIQUE_IDS, VISIBLE_STYLES

StylePresetId = Literal[*VISIBLE_STYLES]
StyleTechniqueId = Literal[*TECHNIQUE_IDS]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Result(StrictModel):
    schema_version: Literal[1] = 1
    data: dict[str, Any]


class ScoreEdit(StrictModel):
    note_id: str = Field(min_length=1, max_length=180)
    pitch: Annotated[int, Field(ge=0, le=127)] | None = None
    onset_tick: Annotated[int, Field(ge=0)] | None = None
    duration_tick: Annotated[int, Field(gt=0)] | None = None


class StyleDetail(StrictModel):
    onset: float = Field(default=1.,ge=0,le=1)
    sustain: float = Field(default=1.,ge=0,le=1)
    connection: float = Field(default=1.,ge=0,le=1)
    release: float = Field(default=1.,ge=0,le=1)


class StyleRegion(StrictModel):
    start: float | None = Field(
        default=None,
        ge=0,
        description='旧秒范围起点；与 end 成对使用，不能和音符 ID 边界混用。',
    )
    end: float | None = Field(
        default=None,
        gt=0,
        description='旧秒范围终点；须大于 start，最终仍扩展到完整音符。',
    )
    start_note_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=180,
        description='当前 SVP 工程 context_read 中首音符的 ID；区间从该音符起点开始并全包含。',
    )
    end_note_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=180,
        description='当前 SVP 工程 context_read 中末音符的 ID；区间包含该音符完整时值。',
    )
    preset: StylePresetId
    strength: float = Field(ge=0, le=1)
    style_pitch: bool = True
    detail: StyleDetail | None = None
    style_technique: StyleTechniqueId = Field(default=DEFAULT_TECHNIQUE_ID,
        validation_alias=AliasChoices('style_technique','technique'),
        description='唱腔字段style_technique；兼容输入别名technique，保存时规范为style_technique。')
    technique_strength: float = Field(default=.6, ge=0, le=1)
    technique_pitch: bool = True
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=128)

    @model_validator(mode='after')
    def validate_exclusive_range(self):
        has_seconds = self.start is not None or self.end is not None
        has_note_ids = (
            self.start_note_id is not None or self.end_note_id is not None
        )
        if has_seconds and has_note_ids:
            raise ValueError('start/end 与 start_note_id/end_note_id 只能选择一种边界模式。')
        if has_seconds:
            if self.start is None or self.end is None:
                raise ValueError('start 与 end 必须同时提供。')
            if self.end <= self.start:
                raise ValueError('end 必须大于 start。')
            return self
        if self.start_note_id is None or self.end_note_id is None:
            raise ValueError(
                '必须提供 start/end，或同时提供 start_note_id/end_note_id。'
            )
        return self

    @model_serializer(mode='wrap')
    def omit_inactive_range_fields(self, handler):
        """Keep the old seconds payload shape while allowing either range mode."""
        return {key: value for key, value in handler(self).items() if value is not None}


class StyleProposal(StrictModel):
    track_indices: list[Annotated[int, Field(ge=0)]] = Field(min_length=1, max_length=128)
    brief: str = Field(min_length=1, max_length=8000)
    regions: list[StyleRegion] = Field(min_length=1, max_length=256)


PreparationExecutionDevice = Literal[*PREPARATION_DEVICE_VALUES]
WorkflowReviewMode = Annotated[Literal['full','segments','legacy'], Field(
    description='full=整曲一次明确审批（默认）；segments=逐段审批；legacy=兼容旧流程。')]


class PreparationOptions(StrictModel):
    language: Literal['zh','ja'] = 'zh'
    lyrics: str = Field(default='', max_length=100000, description=(
        '已核对的主唱歌词；新歌曲开始准备或扒谱前优先填写，以便参与歌词对齐。'
        '没有可靠歌词时可留空，外部搜索结果须先核实版本。'))
    backing_lyrics: str = Field(default='', max_length=100000, description=(
        '已核对的和声歌词；不确定时留空，勿猜测声部文字。'))
    recognize_lyrics: bool = True
    voice_mode: Literal['single','dual'] = Field(default='dual',
        description='single=混合人声；dual=主唱＋和声/伴奏人声。两者都不是按歌手音色拆分对唱。')
    midi_backend: Literal['v1','v2'] | None = None
    alignment_engine: Literal['hfa','tifa'] = 'hfa'
    midi_steps: Literal[8,16,32] = 16
    bpm: float | None = Field(default=None, ge=PREPARATION_BPM_RANGE[0],
        le=PREPARATION_BPM_RANGE[1])
    separation_device: PreparationExecutionDevice | None = Field(default=None,
        description='MDX/声部分离执行设备，仅支持 cpu 或 dml；RoFormer 设备是独立设置。')
    midi_device: PreparationExecutionDevice | None = Field(default=None,
        description='Vocal2MIDI 执行设备，仅支持 cpu 或 dml；RoFormer 设备是独立设置。')
    cpu_threads: int | None = Field(default=None, ge=PREPARATION_CPU_THREADS_RANGE[0],
        le=PREPARATION_CPU_THREADS_RANGE[1], description='0 表示自动；最大 64。')


class LyricEdit(StrictModel):
    """Note-owned text edit kept for the legacy note-level lyrics tool."""
    note_id: str = Field(min_length=1, max_length=180)
    lyric: str = Field(max_length=256)
    operation: Literal['replace','insert']


class MoveLyricEvent(StrictModel):
    """Move one exact source lyric event to a uniquely-owned note onset."""
    event_id: str = Field(min_length=1, max_length=180)
    target_note_id: str = Field(min_length=1, max_length=180)
    operation: Literal['move_event']


class DeleteLyricEvent(StrictModel):
    """Delete one exact source lyric event without guessing an owner."""
    event_id: str = Field(min_length=1, max_length=180)
    operation: Literal['delete_event']


LyricsEdit = Annotated[
    LyricEdit | MoveLyricEvent | DeleteLyricEvent,
    Field(discriminator='operation'),
]


class ExportReview(StrictModel):
    report_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['accept','needs_revision']
    rationale: str = Field(min_length=1, max_length=8000)
    accepted_loss_codes: list[str] = Field(default_factory=list, max_length=128)


class DeleteNote(StrictModel):
    operation: Literal['delete']
    note_id: str = Field(min_length=1,max_length=180)
    lyric_policy: Literal['preserve','delete_owned']


class InsertNote(StrictModel):
    operation: Literal['insert']
    track_index: int = Field(ge=0)
    channel: int = Field(ge=0,le=15)
    pitch: int = Field(ge=0,le=127)
    onset_tick: int = Field(ge=0)
    duration_tick: int = Field(gt=0)
    velocity: int = Field(ge=1,le=127)
    lyric: str | None = Field(default=None,max_length=256)


StructureEdit = Annotated[DeleteNote | InsertNote,Field(discriminator='operation')]


class PronunciationEdit(StrictModel):
    note_ref: str = Field(min_length=1,max_length=256)
    phonemes: str = Field(max_length=256)


class PronunciationTarget(StrictModel):
    artifact_id: str = Field(min_length=1,max_length=80)
    expected_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    edits: list[PronunciationEdit] = Field(min_length=1,max_length=256)
