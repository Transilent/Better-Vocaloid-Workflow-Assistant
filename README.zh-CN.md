<p align="center"><img src="assets/app.svg" width="80" alt="VWA 图标"></p>

# VWA · 术力口工作流助手

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

VWA 是面向 Windows 的歌声合成工作流助手，串联歌曲准备、逐句调音、简易混音、视频合成与投稿资料整理。

**用户交流群：QQ群 490619616。** 欢迎交流使用体验、问题与建议。

[问题反馈与建议](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose) · [Issue 与 PR 贡献规范](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/blob/main/CONTRIBUTING.zh-CN.md)

**[下载 VWA 2.0.0（Windows x64）](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/tag/v2.0.0)** · [使用指南](docs/UserGuide.zh-CN.md)

2.0.0 采用 Qt Quick 工作台。调音可在单轨卷帘和多轨编排间切换，混音支持整曲连续试听和实时分析。首次启动有可跳过的教程；发现更新时先显示版本和更新内容，由你决定何时更新。详见[启动提醒与教程](docs/StartupExperience.zh-CN.md)。

## 功能

| 工作流 | 用途 |
| --- | --- |
| 准备工作流 | 导入本地音频或 B站 BV 号／链接，分离主唱、和声与伴奏，识别中文或日语歌词与音符，导出 MIDI、USTX、VSQX 或 SVP。 |
| 调音工作流 | 选择已有任务或导入 SVP／USTX／VPR，按歌词和轨道叠加实验风格、时间片段与局部音高表现，另存同格式工程。 |
| 简易混音 | 平衡最多三条歌声 WAV 和一条伴奏，支持轨道音量、声像、静音／独奏、歌声时间偏移及混响发送，共用 EQ／压缩／混响，并提供可编辑的 A/B 试听。 |
| 合成视频 | 将视频与 Mixdown 合成，支持自动对齐、手动偏移和可选响度处理。 |
| 发布 | 整理封面、标题、简介与署名；B站支持辅助上传，小红书提供本地资料、文案复制与 AI 声明确认，由用户在官方端手动投稿。 |
| Agent／MCP（可选） | 让兼容宿主连接 VWA 的 12 个具名顶层工具，覆盖准备、证据审阅、决定、导出、恢复与交付；安装独立于桌面应用。 |

调音支持保留原表现、参考演唱映射、实测声库校准、沿用已有参数、时间片段风格、风格自动暂存和独立的中文戏腔实验层。参考映射与柔和、悲伤、明亮欢快三种实验风格只生成可编辑草案，需在目标声库中合成并试听。实测校准对应当前乐谱与声库设置，需要用户在 SV 中导出试音。声库合成在相应编辑器中进行。

[简易混音](docs/SimpleMixingComponent.zh-CN.md)使用单一原生连续 PCM 试听，切页与最小化保持同一播放会话。轨道电平、EQ 频谱、增益衰减估计与混响读数来自实际试听数据；设备失败会暂停，需明确点击播放重试。请在目标声库和音频设备上试听完整歌曲。源文件保留。

此外提供任务记录与恢复、处理预设、可选模型安装，以及 SRT／WebVTT 字幕导出。界面支持简体中文、日语和英语，可切换明暗主题与界面缩放。MCP 技术预览为独立可选安装，不提供任意命令执行器，不使用 MCP 时桌面应用功能不受影响。

## 下载与安装

在 [2.0.0 发行页](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/tag/v2.0.0)的 **Assets** 中选择普通版或 JPN 版。

| 包型 | 用途与内容 |
| --- | --- |
| 全量包 | 面向所有用户的干净安装路线，包括新用户和任意旧版本用户；包含完整模型集与基础运行环境。 |
| JPN 包 | 工作流与全量包相同，仅省略中文 Qwen 权重，保留日语识别与音符提取；中文模型可后续安装。 |
| 增量更新 | 仅面向正式发行的 **1.0.0 和 1.1.0**，从旧版设置页启动。 |

两种全量变体均支持三种界面语言。界面语言与歌词识别语言分别设置。GitHub 的 **Source code** 源码包和更新 ZIP 均不能作为完整安装使用。

**普通版（约 5.41 GB）：** 下载 `VWA-Windows-x64.zip.001`、`.002`、`.003`、`portable-metadata-full.json` 和 `Merge-PortableParts.ps1`，放在同一文件夹。在该文件夹打开 PowerShell，运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Merge-PortableParts.ps1
```

脚本会校验三卷并生成 `VWA-Windows-x64.zip`。**JPN 版（约 1.74 GB）** 下载 `VWA-Windows-x64-JPN.zip`，可直接解压。建议使用较短的可写路径，例如 `D:\Apps`；在解压出的 `VWA` 文件夹内运行 **`VWA.exe`**。移动时请保留整个文件夹。

运行环境为 Windows 10／11 x64，便携包已包含运行环境。最终下载、合并与解压所需空间以正式发行说明为准，模型组件和素材另需空间。声库合成需自行准备兼容的歌声编辑器与声库。

## 使用流程

1. 在**准备工作流**选择音乐来源、歌词语言、处理方式与保存目录，完成后核对音频、音符和歌词。
2. 在**调音工作流**打开任务工程，或导入 Synthesizer V Studio 1／2 的 SVP、OpenUtau 的 USTX 或 VOCALOID 5／6 的 VPR。按歌词定位、选轨、调整风格，另存为新工程，在对应编辑器中合成和试听。
3. 合成歌声干声后，在**简易混音**中平衡最多三条歌声与一条伴奏，试听并导出新 WAV，或显式登记为任务 Mixdown。
4. 在**合成视频**中选择原视频和 Mixdown；已有成品视频可直接进入**发布**。
5. 核对封面、平台文案与署名后准备上传，最后在创作页面检查并手动发布。

调音可直接打开已支持的 Synthesizer V Studio 2（SV2）原生 SVP。独占的素材库音符组可用「保留原表现」叠加风格并另存候选；共享组、裁掉音符的组或尚未验证的工程格式会显示为只读。请在 SV2 中打开候选试听。

OpenUtau USTX 可在「保留原表现」下叠加响度（DYN）与音高偏差（PITD），保留音符内的音高、颤音、音素和其他表情。完成后另存 USTX，再用 OpenUtau 打开试听。USTX 0.5 可预览，编辑前请先在 OpenUtau 中另存为较新格式。

VOCALOID 5/6 VPR 可在「保留原表现」下叠加力度（DYN）与音高弯曲（PIT），保留 PBS、声库、原生音高和内嵌音频。重叠音符保持原样；已手绘音高的音符不叠加 PIT。完成后另存 VPR，再用 VOCALOID 打开试听。

在**任务与结果**查找、继续或重试任务，在**模型与组件**按需安装模型。到**设置与支持**调整外观和界面语言、重看教程或管理[本机预设库](docs/PresetLibrary.zh-CN.md)。预设可导出分享，导入默认追加。

## 升级

**1.0.0 或 1.1.0 正式版：** 在旧版的**设置与更新**中检查更新，按提示重启安装 2.0.0。普通版和 JPN 均可升级，保留设置、任务、预设、登录会话、已安装模型与可选组件。JPN 不会自动补装原来未安装的中文模型。

**其他版本：** 把普通版或 JPN 完整包解压到新目录。升级前请备份正在编辑的工程，通过应用更新，勿手工解压增量 ZIP 覆盖。

## 文档与反馈

[使用指南](docs/UserGuide.zh-CN.md) · [调音说明](docs/TuningWorkflow.zh-CN.md) · [便携版说明](docs/Portable.zh-CN.md) · [MCP 12 工具说明](docs/AgentMCPStandard.zh-CN.md) · [更新记录](CHANGELOG.md) · [问题反馈](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose)

## 项目致谢

感谢以下项目的作者与贡献者。

| 项目 | 在 VWA 中的用途 |
| --- | --- |
| [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi) | 音符识别与歌词对齐。 |
| [LibreSVIP](https://github.com/SoulMelody/LibreSVIP) | USTX、VSQX、SVP 工程转换。 |
| [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui)／[UVR 模型仓库](https://github.com/TRvlvr/model_repo) | 人声分离、模型权重与参数。 |
| [Pymss](https://github.com/pymss-project/pymss)／[becruily BS-RoFormer karaoke](https://huggingface.co/becruily/bs-roformer-karaoke) | 可选分离引擎与模型。 |
| [Pymss Studio](https://github.com/pymss-project/pymss-studio) | 界面及模型管理设计参考。 |
| [PyWORLD](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder)／[WORLD](https://github.com/mmorise/World) | 参考演唱声学分析。 |
| [163MusicLyrics](https://github.com/jitwxs/163MusicLyrics) | 歌词查询行为参考。 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 媒体下载。 |
| [FFmpeg](https://ffmpeg.org/)／[Jellyfin FFmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) | 音视频处理。 |
| [Playwright for Python](https://github.com/microsoft/playwright-python) | 浏览器登录与上传辅助。 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime)／[PyTorch](https://pytorch.org/) | 模型运行。 |
| [Qt Quick 和 PySide6](https://doc.qt.io/qtforpython-6.10/) | 桌面界面。 |
| [NumPy](https://numpy.org/)、[SciPy](https://scipy.org/)、[librosa](https://librosa.org/)、[SoundFile](https://github.com/bastibe/python-soundfile)、[Mido](https://github.com/mido/mido)、[Pillow](https://python-pillow.org/) | 数值、音频、MIDI 与图像处理。 |

所集成的 Vocal2Midi 流程还使用 [GAME](https://github.com/openvpi/GAME)、[HubertFA](https://github.com/wolfgitpr/HubertFA)、[LyricFA](https://github.com/wolfgitpr/LyricFA)、[RomajiASR](https://github.com/Xiantaidu/RomajiASR)、[llama.cpp](https://github.com/ggml-org/llama.cpp) 及相关语音和音高模型，更多致谢见保留的[上游说明](dependencies/vocal2midi2/ACKNOWLEDGEMENTS.md)。

## 许可证

VWA 原创代码采用 [MIT 许可证](LICENSE)。第三方代码、运行库与模型权重分别保留其原有许可证与声明，详见[第三方说明](THIRD_PARTY_NOTICES.zh-CN.md)。

[2.0.0 随包运行库、许可和对应源码](docs/ThirdPartyRuntime.md)。
