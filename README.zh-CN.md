# Better Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md)

面向 Windows x64 的调音流程助手。从 B站视频或本地音乐开始，分离人声与伴奏、提取带歌词的 MIDI；人工调音及剪辑完成后，可准备封面、来源及上传资料。

## 快速开始

1. 从 [Releases](../../releases) 下载完整便携包。自动生成的 **Source code** 压缩包不包含运行库和模型。
2. 将完整 `BVWA` 文件夹解压到可写的短路径，例如 `D:\BVWA`。
3. 双击唯一的启动器 **`Start-Assistant.bat`**。
4. 选择 BV / B站链接或本地音乐，设置分离方式和可选歌词，开始处理。
5. 将 MIDI 和伴奏导入调音软件；调音及 mixdown 与视频的对齐由人完成。
6. 在发布页选择成品，编辑标题、来源并登录平台，核对上传页面后手动发布。

## 功能

| 输入或方式 | 处理 | 主要结果 |
| --- | --- | --- |
| BV / B站链接 | 获取视频、音频、来源与本家封面 | 来源素材与简介草稿 |
| 本地 WAV / MP3 / FLAC / M4A | 保存副本并解码，无需 BV | 统一格式的原音频 |
| 模型双轨 | 分离人声 / 伴奏，再拆分主唱 / 和声 | 主唱、和声、伴奏、双声部 MIDI |
| 外部分轨 | 导入完整主唱、和声、伴奏 WAV | 双声部 MIDI |
| 合并人声单轨 | 提取合并人声的音符和歌词 | 单声部 MIDI |
| 上传准备 | 填写标题、来源、封面 | 待人工核对的 B站 / 小红书页面 |

模型通过命令行在本地运行，网页操作使用 Playwright 页面控件。分离可能串音，音符和歌词需要试听校正。调音、成品对齐和最终发布由人完成。

本家原始封面指来源视频在 B站列表上展示的图片，通过视频资料获取。视频截图为主动选择的替代项。4:3 封面补边保留完整图片，16:9 保留原比例。

## 便携包

包内含 Python、推理代码和模型、FFmpeg、浏览器及自动化依赖。目标平台为 Windows 10/11 x64，推荐至少 16 GB 内存、12 GB 可用空间。显卡加速取决于硬件；CPU 模式速度较慢。

GitHub 附件为分卷：将全部 `.zip.00x` 和 `release-assets.json` 下载到同一目录，用 7-Zip 打开 `.zip.001`，或使用 `Merge-PortableParts.ps1` 校验合并。先解压，再启动。

| 批处理文件 | 用途 |
| --- | --- |
| `Start-Assistant.bat` | 唯一的程序启动入口 |
| `Check-Dependencies.bat` | 校验固定依赖 |
| `Use-CPU.bat` | 选择 CPU 推理，重启后生效 |
| `Use-GPU.bat` | 选择 DirectML 推理，重启后生效 |

不再提供重复的 `.cmd` 启动器。每台新电脑需重新登录 B站和小红书。

## 源码与资料

Git 保存助手源码、冻结的集成代码、测试、配置、打包工具和依赖清单；模型及大型二进制存于私有 Release。开发时可先解压便携包，再覆盖仓库源码。

仓库和发行包不含账号会话、令牌、私人音视频、任务历史或发布草稿。运行数据保存于 `cache/`、`jobs/`、`publish-packages/`。

详见[使用指南](docs/UserGuide.zh-CN.md)、[结构说明](docs/Architecture.zh-CN.md)、[验证说明](docs/Verification.zh-CN.md)和[第三方说明](THIRD_PARTY_NOTICES.zh-CN.md)。

## 相关项目与致谢

助手集成了已有工具和模型流程，感谢各项目的作者与贡献者。

| 项目 | 在助手中的用途 |
| --- | --- |
| [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi) | 通过内置应用层提取音符、对齐歌词并输出 MIDI；仓库许可为 Apache-2.0 |
| [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) 与 [UVR 模型仓库](https://github.com/TRvlvr/model_repo) | MDX 推理参考、分离权重及参数；无需运行 UVR 桌面软件 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | B站媒体下载 |
| [FFmpeg](https://ffmpeg.org/) / [Jellyfin FFmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) | 音频解码、媒体检测、视频截图 |
| [Playwright for Python](https://github.com/microsoft/playwright-python) | 浏览器登录及创作页面操作 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | CPU / DirectML 推理 |
| [PyQt](https://www.riverbankcomputing.com/software/pyqt/) | 桌面界面 |
| [NumPy](https://numpy.org/)、[SciPy](https://scipy.org/)、[librosa](https://librosa.org/)、[SoundFile](https://github.com/bastibe/python-soundfile)、[Mido](https://github.com/mido/mido)、[Pillow](https://python-pillow.org/) | 音频、数值、MIDI 和图像处理 |

Vocal2Midi 流程还使用 [GAME](https://github.com/openvpi/GAME)、[HubertFA](https://github.com/wolfgitpr/HubertFA)、[LyricFA](https://github.com/wolfgitpr/LyricFA)、[llama.cpp](https://github.com/ggml-org/llama.cpp) 及其语音、音高模型。详细致谢保留于[上游说明](dependencies/vocal2midi/ACKNOWLEDGEMENTS.md)，组件授权见[第三方说明](THIRD_PARTY_NOTICES.zh-CN.md)。各项目与模型保留独立许可证，此列表不表示整个包采用同一授权。
