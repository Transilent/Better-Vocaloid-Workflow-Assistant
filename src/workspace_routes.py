"""Stable workspace page registry shared by Qt 5, Qt 6 and non-GUI callers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PageSpec:
    """One page plus its stable ID, routing aliases and sidebar projection."""

    page_id: str
    legacy_index: int
    title: str
    sidebar_order: int = None
    sidebar_gap_before: bool = False
    english_title: str = None
    target: str = None
    focus_step: str = None


PAGE_SPECS = (
    PageSpec(page_id='processing', legacy_index=0, title='准备工作流', sidebar_order=0),
    PageSpec(page_id='components', legacy_index=1, title='模型与组件', sidebar_order=5,
             sidebar_gap_before=True, english_title='Models'),
    PageSpec(page_id='results', legacy_index=2, title='任务与结果', sidebar_order=6,
             english_title='Tasks and results'),
    PageSpec(page_id='publishing', legacy_index=3, title='发布', sidebar_order=4),
    PageSpec(page_id='updates', legacy_index=4, title='设置与更新', sidebar_order=7,
             english_title='Settings'),
    PageSpec(page_id='workflow', legacy_index=5, title='工作流', target='processing'),
    PageSpec(page_id='storage', legacy_index=6, title='缓存与磁盘', target='updates'),
    PageSpec(page_id='subtitles', legacy_index=7, title='歌词转字幕', target='processing',
             focus_step='subtitles'),
    PageSpec(page_id='video_mixdown', legacy_index=8, title='合成视频', sidebar_order=3),
    PageSpec(page_id='tuning', legacy_index=9, title='调音工作流', sidebar_order=1),
    PageSpec(page_id='audio_mixdown', legacy_index=10, title='简易混音', sidebar_order=2),
)


__all__ = ('PageSpec', 'PAGE_SPECS')