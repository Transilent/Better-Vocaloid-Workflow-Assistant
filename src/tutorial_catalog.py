"""Single source of truth for onboarding scenarios, non-blocking tour steps, What's New, and shortcuts.

Zero Qt imports so it stays testable in pure-Python L0 runs and eliminates drift
between Python step counts and QML UI definitions.
"""
from __future__ import annotations

FEATURE_HIGHLIGHTS_VERSION = "3.0.0"

SCENARIO_CARDS: tuple[dict[str, str], ...] = (
    {
        "id": "prep",
        "pageId": "processing",
        "icon": "waveform",
        "title": "扒谱与歌曲准备",
        "summary": "输入 B 站链接或本地音频，一键人声分离、提取 MIDI 音高与歌词，并生成主流工程。",
        "badge": "最常用",
    },
    {
        "id": "chorus",
        "pageId": "chorus",
        "icon": "pitch_curve",
        "title": "人声修音与合唱对齐",
        "summary": "拖入干声与参考 MIDI，一键批量对齐节奏并修准音高，支持逐句手绘音高与音节力度精修。",
        "badge": "新工作台",
    },
    {
        "id": "tuning_mix",
        "pageId": "tuning",
        "icon": "music_note",
        "title": "风格调音与简易混音",
        "summary": "原生打开 SVP / USTX / VPR / VSQX / ACEP / UFData 工程调音，随后送入实时混音与出片。",
        "badge": "全流程",
    },
    {
        "id": "convert",
        "pageId": "project_convert",
        "icon": "swap",
        "title": "工程格式安全互转",
        "summary": "在 12 种歌声合成工程间先体检再互转，绝不覆盖源文件，并支持按音符时间轴导出 LRC / SRT。",
        "badge": "新工具",
    },
)

TOUR_STEPS: tuple[dict[str, str], ...] = (
    {
        "pageId": "processing",
        "title": "准备工作流",
        "body": "从一首歌开始：粘贴哔哩哔哩链接或拖入本地音频，按需填写歌词与导出格式，自动完成分离、MIDI 与工程生成。",
        "shortcut": "Ctrl+1",
    },
    {
        "pageId": "results",
        "title": "任务与结果",
        "body": "所有任务的历史进度、分轨音频、工程文件与核验状态都在这里集中管理，可一键流转到调音、混音或合成视频。",
        "shortcut": "Ctrl+2",
    },
    {
        "pageId": "tuning",
        "title": "调音工作流",
        "body": "原生打开 SVP / USTX / VPR / VSQX / ACEP / UFData 工程，按片段叠加风格与参数来源，预览变化后另存独立候选工程。",
        "shortcut": "Ctrl+3",
    },
    {
        "pageId": "tuning_workshop",
        "title": "调音工坊与声库校准",
        "body": "自定义情绪配方、管理个人风格预设，或通过七轨试音拟合不同声库的实测参数响应。",
        "shortcut": "Ctrl+4",
    },
    {
        "pageId": "chorus",
        "title": "修音工作台",
        "body": "拖入多轨干声与背景 MIDI，对照乐谱批量对齐节奏和音高；支持自由手绘音高线、拆音符、按音节补力度与分轨交付。",
        "shortcut": "Space / N",
    },
    {
        "pageId": "project_convert",
        "title": "工程互转",
        "body": "在主流歌声合成工程之间安全互转，自动体检轨道、BPM 与音高曲线并回读校验，同时支持导出滚动 LRC / SRT 歌词。",
        "shortcut": "Ctrl+K",
    },
    {
        "pageId": "audio_mixdown",
        "title": "简易混音",
        "body": "实时调整人声、和声与伴奏的电平、声像、EQ、柔化齿音、压限与混响，先试听对比响度再无损导出。",
        "shortcut": "Space",
    },
    {
        "pageId": "video_mixdown",
        "title": "合成视频",
        "body": "拖入原视频与新混音音频，自动提取高清首帧、对齐音轨并无损封装成 MP4 成片。",
        "shortcut": "",
    },
    {
        "pageId": "publishing",
        "title": "发布准备",
        "body": "整理成品视频、封面与平台文案，按平台手动核对后安全交接；所有平台操作始终由你最终确认。",
        "shortcut": "",
    },
    {
        "pageId": "components",
        "title": "模型与组件",
        "body": "集中查看分离模型、AI 推理组件与本地运行库状态，缺少组件时可一键下载或切换国内加速源。",
        "shortcut": "",
    },
    {
        "pageId": "updates",
        "title": "设置与支持",
        "body": "切换浅色/深色主题、中英日界面语言、渲染后端与网络代理，随时清理缓存、导出脱敏诊断包或重看本教程。",
        "shortcut": "Ctrl+/",
    },
)

WHATS_NEW_HIGHLIGHTS: tuple[dict[str, str], ...] = (
    {
        "pageId": "chorus",
        "title": "全新修音工作台（Chorus Workbench）",
        "summary": "支持多轨干声与 MIDI 对照修音、手绘音高曲线、按音节补力度、Signalsmith 高保真重采样与分轨交付。",
    },
    {
        "pageId": "project_convert",
        "title": "12 格式工程安全互转与时间轴歌词导出",
        "summary": "内置 SVP / USTX / VPR / VSQX / ACEP / UFData / MIDI 等互转与回读校验，一键生成 LRC / SRT。",
    },
    {
        "pageId": "updates",
        "title": "全局快捷命令（Ctrl+K）与快捷键速查（Ctrl+/）",
        "summary": "随时呼出命令面板跨页跳转、一键切换主题或导出脱敏诊断包，常用工作区支持秒切保活。",
    },
)

SHORTCUT_GROUPS: tuple[dict[str, object], ...] = (
    {
        "group": "全局导航与效率",
        "items": [
            {"keys": "Ctrl + K", "desc": "打开全局快捷命令面板"},
            {"keys": "Ctrl + /  或  F1", "desc": "打开快捷键速查表"},
            {"keys": "Ctrl + 1 ~ 9", "desc": "按侧边栏顺序快速切换工作区"},
            {"keys": "Esc", "desc": "关闭当前弹窗或取消选区"},
        ],
    },
    {
        "group": "修音工作台",
        "items": [
            {"keys": "Space", "desc": "播放 / 暂停实时试听"},
            {"keys": "N", "desc": "跳转下一处待核对唱段或偏调音符"},
            {"keys": "Ctrl + Z / Ctrl + Y", "desc": "撤销 / 重做修音与对齐操作"},
            {"keys": "Ctrl + S", "desc": "保存当前修音工程"},
        ],
    },
    {
        "group": "调音钢琴卷帘",
        "items": [
            {"keys": "左 / 右方向键", "desc": "选中相邻音符"},
            {"keys": "F2  或  双击音符", "desc": "原地修改音符歌词"},
            {"keys": "中键拖动 / Shift + 滚轮", "desc": "平移时间轴与横移视口"},
            {"keys": "Delete", "desc": "删除当前选中的风格片段"},
        ],
    },
    {
        "group": "新手教程与弹窗",
        "items": [
            {"keys": "左 / 右方向键", "desc": "切换上一步 / 下一步导览"},
            {"keys": "Enter", "desc": "确认当前高亮命令或对话框"},
        ],
    },
)
