<p align="center"><img src="assets/app.svg" width="80" alt="VWA 图标"></p>

# VWA · 术力口工作流助手

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

VWA 是面向 Windows 的歌声合成工作流助手，提供歌曲准备、SVP 调音、视频合成和上传资料整理。

## 功能

| 工作流 | 用途 |
| --- | --- |
| 准备工作流 | 导入本地音频或 B站 BV 号／链接，分离主唱、和声与伴奏，识别中文或日语歌词与音符，导出 MIDI、USTX、VSQX 或 SVP。 |
| 调音工作流 | 选择已有任务或导入 SVP，指定演唱轨和参数来源，按需叠加演唱风格与局部音高表现，另存独立工程。 |
| 合成视频 | 将视频与 Mixdown 合成，支持自动对齐、手动偏移和可选响度处理。 |
| 发布 | 整理封面、标题、简介与署名，辅助 B站和小红书上传，最终在平台页面确认发布。 |

调音支持保留原表现、参考演唱映射、实测声库校准和沿用已有参数。参考映射与风格预设生成可编辑草案，需在目标声库中试听。实测校准对应当前乐谱与声库设置，需要用户在 SV 中导出试音。声库合成和混音在相应编辑器及音频软件中完成。

此外提供任务记录与恢复、处理预设、可选模型安装，以及 SRT／WebVTT 字幕导出。界面支持简体中文、日语和英语，可切换明暗主题与界面缩放。

## 下载与安装

从 [Releases](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/tag/v1.0.0) 下载 **1.0.0**。

| 包型 | 下载大小 | 内容 |
| --- | ---: | --- |
| 全量包 | 约 4.91 GiB，分三卷 | 包含中文、日语识别模型与基础运行环境。 |
| [JPN 包](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/download/v1.0.0/VWA-Windows-x64-JPN.zip) | 约 1.53 GiB | 仅省略三份中文 Qwen 权重，保留日语识别与音符提取；中文模型可后续安装。 |

两种包均支持三种界面语言。界面语言与歌词识别语言分别设置。GitHub 的 **Source code** 源码包和更新 ZIP 均不能作为完整安装使用。

**全量包：**将以下五个文件下载到同一文件夹：

- `VWA-Windows-x64.zip.001`、`.002`、`.003`
- `portable-metadata-full.json`
- `Merge-PortableParts.ps1`

在该文件夹打开 PowerShell，运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Merge-PortableParts.ps1
```

脚本校验分卷并生成 `VWA-Windows-x64.zip`，随后完整解压。JPN 包可直接解压下载的 ZIP。进入解压后的 `VWA` 文件夹，运行 **`VWA.exe`**。迁移程序时保留整个文件夹。

运行环境为 Windows 10／11 x64，便携包已包含运行环境。全量包下载、合并与解压建议预留约 25 GB，模型组件和素材另需空间。声库合成需自行准备兼容的歌声编辑器与声库。

## 使用流程

1. 在**准备工作流**选择音乐来源、歌词语言、处理方式与保存目录，完成后核对音频、音符和歌词。
2. 在**调音工作流**选择已有任务或导入 SVP，指定轨道、参数来源与可选风格，生成独立工程并在 SV 中试听。
3. 完成人声合成与混音后，在**合成视频**中选择原视频和 Mixdown；已有成品视频可直接进入**发布**。
4. 核对封面、平台文案与署名后准备上传，最后在创作页面检查并手动发布。

在**任务与结果**查找、继续或重试任务，在**模型与组件**按需安装模型。界面语言可在**设置与更新 → 外观 → 界面语言**中修改，下次启动生效。

## 升级

| 当前版本 | 升级方式 |
| --- | --- |
| 0.4.0／0.4.1 | 按**设置与更新**提供的升级路径操作。 |
| 0.3.0／0.3.1 | 运行 [VWA-Updater.exe](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/download/v1.0.0/VWA-Updater.exe)，按提示升级。 |

支持的升级路径保留设置、任务、登录会话、已安装模型及可选组件。升级前请备份正在编辑的工程。发行页中的 `BVWA` 文件供旧客户端兼容使用。

## 文档与反馈

[使用指南](docs/UserGuide.zh-CN.md) · [调音说明](docs/TuningWorkflow.zh-CN.md) · [更新记录](CHANGELOG.md) · [问题反馈](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues)

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
| [PyQt](https://www.riverbankcomputing.com/software/pyqt/) | 桌面界面。 |
| [NumPy](https://numpy.org/)、[SciPy](https://scipy.org/)、[librosa](https://librosa.org/)、[SoundFile](https://github.com/bastibe/python-soundfile)、[Mido](https://github.com/mido/mido)、[Pillow](https://python-pillow.org/) | 数值、音频、MIDI 与图像处理。 |

所集成的 Vocal2Midi 流程还使用 [GAME](https://github.com/openvpi/GAME)、[HubertFA](https://github.com/wolfgitpr/HubertFA)、[LyricFA](https://github.com/wolfgitpr/LyricFA)、[RomajiASR](https://github.com/Xiantaidu/RomajiASR)、[llama.cpp](https://github.com/ggml-org/llama.cpp) 及相关语音和音高模型，更多致谢见保留的[上游说明](dependencies/vocal2midi2/ACKNOWLEDGEMENTS.md)。

## 许可证

VWA 原创代码采用 [MIT 许可证](LICENSE)。第三方代码、运行库与模型权重分别保留其原有许可证与声明，详见[第三方说明](THIRD_PARTY_NOTICES.zh-CN.md)。
