"""Shared strict request/response models for VWA MCP profiles."""
from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from agent_preparation import PREPARATION_BPM_RANGE, PREPARATION_CPU_THREADS_RANGE, PREPARATION_DEVICE_VALUES

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
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    preset: Literal['none', 'gentle', 'sad', 'bold']
    strength: float = Field(ge=0, le=1)
    style_pitch: bool = True
    detail: StyleDetail | None = None
    style_technique: Literal['none', 'chinese_opera'] = Field(default='none',
        validation_alias=AliasChoices('style_technique','technique'),
        description='唱腔字段style_technique；兼容输入别名technique，保存时规范为style_technique。')
    technique_strength: float = Field(default=.6, ge=0, le=1)
    technique_pitch: bool = True
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=128)


class StyleProposal(StrictModel):
    track_indices: list[Annotated[int, Field(ge=0)]] = Field(min_length=1, max_length=128)
    brief: str = Field(min_length=1, max_length=8000)
    regions: list[StyleRegion] = Field(min_length=1, max_length=256)


PreparationExecutionDevice = Literal[*PREPARATION_DEVICE_VALUES]
WorkflowReviewMode = Annotated[Literal['full','segments','legacy'], Field(
    description='full=整曲一次明确审批（默认）；segments=逐段审批；legacy=兼容旧流程。')]


class PreparationOptions(StrictModel):
    language: Literal['zh','ja'] = 'zh'
    lyrics: str = Field(default='', max_length=100000)
    backing_lyrics: str = Field(default='', max_length=100000)
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
    note_id: str = Field(min_length=1, max_length=180)
    lyric: str = Field(max_length=256)
    operation: Literal['replace','insert']


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
