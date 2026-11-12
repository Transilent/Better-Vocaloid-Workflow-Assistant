<p align="center"><img src="assets/app.svg" width="80" alt="VWA 图标"></p>

# VWA · 术力口工作流助手

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

VWA 是面向 Windows 的歌声合成工作流助手，提供歌曲准备、SVP 调音、视频合成和上传资料整理。

**用户交流群：QQ群 490619616。** 欢迎交流使用体验、问题与建议。

[问题反馈与建议](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose) · [Issue 与 PR 贡献规范](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/blob/main/CONTRIBUTING.zh-CN.md)

当前开发源码采用 Qt 6 和 Qt Quick，提供可跳过的首次使用教程和可延后的启动更新提醒；设置中可复制群号。详见[启动提醒与教程说明](docs/StartupExperience.zh-CN.md)。已发布的 1.1.0 安装包未变。

## 功能

| 工作流 | 用途 |
| --- | --- |
| 准备工作流 | 导入本地音频或 B站 BV 号／链接，分离主唱、和声与伴奏，识别中文或日语歌词与音符，导出 MIDI、USTX、VSQX 或 SVP。 |
| 调音工作流 | 选择已有任务或导入 SVP／USTX，按歌词和轨道叠加实验风格、时间片段与局部音高表现，另存同格式工程。 |
| 简易混音 | 平衡最多三条歌声 WAV 和一条伴奏，支持轨道音量、声像、静音／独奏、歌声时间偏移及混响发送，共用 EQ／压缩／混响，并提供可编辑的 A/B 试听。 |
| 合成视频 | 将视频与 Mixdown 合成，支持自动对齐、手动偏移和可选响度处理。 |
| 发布 | 整理封面、标题、简介与署名；B站支持辅助上传，小红书提供本地资料、文案复制与 AI 声明确认，由用户在官方端手动投稿。 |
| Agent／MCP（可选） | 让兼容宿主连接 VWA 的 12 个具名顶层工具，覆盖准备、证据审阅、决定、导出、恢复与交付；安装独立于桌面应用。 |

调音支持保留原表现、参考演唱映射、实测声库校准、沿用已有参数、时间片段风格、风格自动暂存和独立的中文戏腔实验层。参考映射与柔和、悲伤、明亮欢快三种实验风格只生成可编辑草案，需在目标声库中合成并试听。实测校准对应当前乐谱与声库设置，需要用户在 SV 中导出试音。声库合成在相应编辑器中进行。

[简易混音](docs/SimpleMixingComponent.zh-CN.md)使用单一原生连续 PCM 试听，切页与最小化保持同一播放会话。轨道电平、EQ 频谱、增益衰减估计与混响读数来自实际试听数据；设备失败会暂停，需明确点击播放重试。请在目标声库和音频设备上试听完整歌曲。源文件保留。

此外提供任务记录与恢复、处理预设、可选模型安装，以及 SRT／WebVTT 字幕导出。界面支持简体中文、日语和英语，可切换明暗主题与界面缩放。MCP 技术预览为独立可选安装，不提供任意命令执行器，不使用 MCP 时桌面应用功能不受影响。

## 下载与安装

从 [1.1.0 发行页](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/tag/v1.1.0)下载适合自己的完整包或 JPN 包。

| 包型 | 用途与内容 |
| --- | --- |
| 全量包 | 面向所有用户的干净安装路线，包括新用户和任意旧版本用户；包含完整模型集与基础运行环境。 |
| JPN 包 | 工作流与全量包相同，仅省略中文 Qwen 权重，保留日语识别与音符提取；中文模型可后续安装。 |
| 更新包 | 只有该安装版本在 1.1.0 发行说明中被明确列为兼容时，才可使用提供的更新；更新包不能作为完整安装。 |

两种全量变体均支持三种界面语言。界面语言与歌词识别语言分别设置。GitHub 的 **Source code** 源码包和更新 ZIP 均不能作为完整安装使用。

**全量包：**若发行时将完整资产分卷，请把同一资产的全部分卷、对应元数据与合并工具放在同一文件夹。在该文件夹打开 PowerShell，运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Merge-PortableParts.ps1
```

脚本校验分卷并生成 `VWA-Windows-x64.zip`，随后完整解压。JPN 若以单个 ZIP 提供，可直接解压。进入解压后的 `VWA` 文件夹，运行 **`VWA.exe`**。迁移程序时保留整个文件夹。

运行环境为 Windows 10／11 x64，便携包已包含运行环境。最终下载、合并与解压所需空间以正式发行说明为准，模型组件和素材另需空间。声库合成需自行准备兼容的歌声编辑器与声库。

## 使用流程

1. 在**准备工作流**选择音乐来源、歌词语言、处理方式与保存目录，完成后核对音频、音符和歌词。
2. 在**调音工作流**选择已有任务或导入 SVP，指定轨道、参数来源与可选实验风格，生成独立工程并在 SV 中试听。
3. 合成歌声干声后，在**简易混音**中平衡最多三条歌声与一条伴奏，试听并导出新 WAV，或显式登记为任务 Mixdown。
4. 在**合成视频**中选择原视频和 Mixdown；已有成品视频可直接进入**发布**。
5. 核对封面、平台文案与署名后准备上传，最后在创作页面检查并手动发布。

调音可直接打开已支持的 Synthesizer V Studio 2（SV2）原生 SVP。独占的素材库音符组可用「保留原表现」叠加风格并另存候选；共享组、裁掉音符的组或尚未验证的工程格式会显示为只读。请在 SV2 中打开候选试听。

OpenUtau USTX 可在「保留原表现」下叠加响度（DYN）与音高偏差（PITD），保留音符内的音高、颤音、音素和其他表情。完成后另存 USTX，再用 OpenUtau 打开试听。

在**任务与结果**查找、继续或重试任务，在**模型与组件**按需安装模型。界面语言可在**设置与更新 → 外观 → 界面语言**中修改，下次启动生效。

## 升级

| 当前版本 | 1.1.0 升级路线 |
| --- | --- |
| 1.0.0 | 在**设置与更新**中检查并安装 1.1.0；1.0.0 JPN 会更新为 1.1.0 JPN。 |
| 0.4.0／0.4.1 | 1.1.0 暂不提供直接更新，请在新目录使用全量包。 |
| 0.3.0／0.3.1 及其他版本 | 请在新目录使用全量包。旧版 `VWA-Updater.exe` 下载不能作为 1.1.0 升级路线。 |

1.0.0 JPN 的更新目标为 1.1.0 JPN：不会为更新强制下载省略的中文权重，用户后来安装的模型会保留。

不要手工解压更新包覆盖安装，只使用应用或发行说明提供的更新。被明确支持的升级会保留设置、任务、登录会话、已安装模型及可选组件。升级前请备份正在编辑的工程。

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
| Qt Quick 和 PySide6 | 当前开发版的桌面界面。 |
| [NumPy](https://numpy.org/)、[SciPy](https://scipy.org/)、[librosa](https://librosa.org/)、[SoundFile](https://github.com/bastibe/python-soundfile)、[Mido](https://github.com/mido/mido)、[Pillow](https://python-pillow.org/) | 数值、音频、MIDI 与图像处理。 |

所集成的 Vocal2Midi 流程还使用 [GAME](https://github.com/openvpi/GAME)、[HubertFA](https://github.com/wolfgitpr/HubertFA)、[LyricFA](https://github.com/wolfgitpr/LyricFA)、[RomajiASR](https://github.com/Xiantaidu/RomajiASR)、[llama.cpp](https://github.com/ggml-org/llama.cpp) 及相关语音和音高模型，更多致谢见保留的[上游说明](dependencies/vocal2midi2/ACKNOWLEDGEMENTS.md)。

## 许可证

VWA 原创代码采用 [MIT 许可证](LICENSE)。第三方代码、运行库与模型权重分别保留其原有许可证与声明，详见[第三方说明](THIRD_PARTY_NOTICES.zh-CN.md)。
