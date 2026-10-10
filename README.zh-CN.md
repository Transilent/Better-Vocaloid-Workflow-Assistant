<p align="center"><img src="assets/app.svg" width="80" alt="BVWA 图标"></p>

# 术力口工作流助手

Better Vocaloid Workflow Assistant (BVWA)

[English](README.md) | [简体中文](README.zh-CN.md)

面向 Windows 的声库调音工作流助手。从本地音乐或 B站视频开始，分离人声与伴奏，生成带歌词的 MIDI；调音完成后，准备成品视频的封面、简介和平台上传。

> **v0.3.0 已发布。** 本版新增可复用工作流、任务管理、字幕工具、无控制台窗口的启动器和校验更新。从 v0.2.0 升级需要完整便携包。

## 功能

- 可拖动、可连线的工作流，处理预设与各引擎 CPU / GPU 设备设置。
- 任务搜索、筛选、排序和管理，分阶段进度、错误恢复与缓存清理。
- LRC / TXT / CSV / MIDI 歌词转 SRT / WebVTT，支持逐行编辑时间。

- 支持 B站 BV 号／链接和本地音频。
- 分离人声与伴奏，分别生成主唱和和声 MIDI。
- 内置 Karaoke 2，可按需安装支持 CPU／NVIDIA GPU 的 BS-RoFormer。
- 支持中文和日语歌词；中文 MIDI 可选择汉字或拼音。
- 支持任务续跑、参考歌词和完整 WAV 分轨导入。
- 获取原视频封面，编辑来源署名，准备 B站和小红书上传。
- 提供无控制台窗口的 EXE 启动器及经过校验的程序更新。

调音、最终音视频对齐和最终发布由用户完成。使用结果前，请试听分离音频并核对音符与歌词。

## 快速开始

1. 从 [Releases](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases) 下载完整便携包。GitHub 自动生成的 **Source code** 是源码，不包含完整运行库。
2. 将全部分卷和 `release-assets.json` 放在同一目录。用 7-Zip 打开 `.001`，或通过 `Merge-PortableParts.ps1` 校验并合并。
3. 解压完整 `BVWA` 文件夹到可写目录，例如 `D:\BVWA`。
4. 双击 **`BVWA.exe`**。
5. 在“处理音乐”中选择工作流预设、添加音乐，点击“开始处理”。

## 从 v0.2.0 升级

v0.3.0 调整了基础运行库清单，需要下载完整便携包。先关闭助手，再解压到新的文件夹。在同一电脑保留数据时，按需从旧目录复制 `config.json`、`jobs/`、`publish-packages/`、`last_job.txt`、`cache/workspace.json` 和 `cache/publish-browser/`。已安装的 BS-RoFormer 可复制 `dependencies/optional/bs-roformer/` 保留。自定义任务目录继续放在原位置。不要用旧程序文件或更新缓存覆盖新版；换电脑后需重新登录发布平台。

程序更新包用于基础运行库相同的安装目录；运行库不同的版本会在“设置与更新”中提示下载完整版。

![处理音乐界面](docs/images/processing.png)

## 保存目录

在“输出与处理设置”点击“选择目录”，或输入新的文件夹路径。默认使用助手目录的 `jobs` 文件夹。每个任务会新建独立子文件夹，保存下载的视频（BV 输入为 `video/source.mp4`）、音频和 MIDI。助手会记住所选目录及以前的任务位置；继续任务沿用原目录，重新处理则在当前选定目录中新建任务。

## 歌词与输出

选择中文后，“中文歌词格式”提供“汉字”和“拼音”。此设置同时用于主唱与和声，并随任务保存。关闭“识别歌词并写入 MIDI”时只导出音符。中文格式选项不影响日语识别。

参考歌词可选填，主唱与和声分别填写，只保留演唱文本。完成后在“任务与结果”查看文件。

| 文件 | 内容 |
| --- | --- |
| `midi/voices.mid` | 时间对齐的主唱、和声双轨 MIDI |
| `midi/lead/lead.mid`、`midi/backing/backing.mid` | 单独声部 MIDI |
| `midi/vocals.mid` | 合并人声模式的结果 |
| `audio/lead.wav`、`audio/backing.wav` | 主唱、和声分离音频 |
| `audio/instrumental.wav` | 伴奏 |

VOCALOID 6 请通过“文件 → 导入”载入 MIDI，提供歌词编码选项时选择 UTF-8。MIDI 使用 120 BPM 换算时间，不代表自动检测了歌曲速度。

## 可选模型与下载源

在“模型与组件”选择运行库和下载方式，点击“下载并安装”；安装完成后选择“使用此模型”。

| 运行库 | 下载量 | 安装后约占用 |
| --- | --- | --- |
| CPU | 0.46 GB | 1.56 GB |
| NVIDIA GPU / CUDA | 3.52 GB | 6.14 GB |

安装支持取消、续传和 SHA-256 校验。中日文使用同一份原始 BS-RoFormer 权重；日语识别保留现有 Romaji ASR。

下载方式包括“自动选择”“官方源直连”和“优先加速源”。自动模式在连接失败后尝试备用源。GitHub 文件支持 [GitProxy](https://gitproxy.dev/guide)，Hugging Face 模型支持 [HF-Mirror](https://hf-mirror.com/)；其他运行库依赖从官方源下载。下载速度和可用性取决于网络与所选服务。

## 工作流、任务与恢复

在**处理音乐**顶部的“工作流预设”中选择内置或自建预设，添加歌曲后点击“开始处理”。流程、模型、语言、歌词格式和设备会一起应用。需要修改时打开**工作流**，保存后点击“用于处理音乐”返回；两个页面的预设选择保持同步。

把左侧模块拖入画布，靠近兼容的前后模块时会显示连接预览；松开后自动吸附并连接，拉远并松开可断开。双击左侧模块会添加到前一个模块之后。点击模块编辑参数，右键可移除模块或连接。**整理画布**按处理顺序排列并连接相邻模块，Ctrl + 滚轮可缩放。

支持的顺序是**音乐输入 → 分离声部 → MIDI → 字幕**。在分离节点结束时只导出音频，不运行 MIDI 提取；字幕节点根据 MIDI 歌词事件生成 SRT 和 WebVTT。缺少连接或顺序错误时会提示修正，不会创建任务。歌曲、BV 和参考歌词在处理页面填写。

选中节点后，可修改语言、模型、歌词格式、提取精度和设备。基础分离与 MIDI 支持 CPU / DirectML；可选 BS-RoFormer 支持自动 / CPU / CUDA。当前参数和自建预设会保存在本机，预设不包含歌曲路径或参考歌词。继续任务使用该任务建立时的参数；**用当前工作流重做**会使用原输入与当前工作流创建新任务。

**任务与结果**支持搜索、状态筛选、排序和重命名。任务菜单提供打开日志、把 MIDI 歌词送入字幕工具和删除任务。删除前会显示目录与占用，并请求确认；删除任务会永久移除对应文件。异常退出后，原任务会标为“已中断”，可继续处理。

进度区域显示当前阶段、声部或模型子任务、已完成阶段和已用时间。能测量的环节显示片段数或下载大小与速度；阶段数量不代表剩余时间估算。失败后可查看原因并重试；设备错误提供“改用 CPU 继续”，磁盘错误可跳转检查占用。已完成且验证通过的输出会复用。

## 缓存管理与歌词字幕

在**缓存与磁盘**扫描占用，勾选缓存或任务临时文件，确认后清理。任务结果、已安装模型、登录信息和预设不会被清理。处理、组件安装或上传准备运行时，缓存操作会等待它们结束。清除任务临时文件后，重试时可能需要重新生成。

**歌词转字幕**支持 UTF-8 的 LRC、TXT、带时间的 CSV（`start,end,text` 三列，单位为秒）及 MIDI 歌词事件。可预览、逐行修改时间与文字、调整整体偏移，再导出 UTF-8 的 SRT 或 WebVTT。LRC 保留原时间戳，支持重复时间、offset 标签与带时间的空行；末行没有结束标记时默认显示 3 秒。MIDI 分句按歌词停顿推断。纯文本没有时间信息，工具会按指定总时长均分作为初始估算，需要人工核对。

便携版清单已排除开发安装器、第三方测试文件、未使用的 Qt Quick 资源和流程不会加载的备用 HFA 文件。现用中日文 ASR、音高与对齐权重保持不变。目前可减少约 447 MB 解压体积，实际压缩大小会在批准打包后测量。

## 程序更新

“设置与更新”提供版本检查、变更说明和默认下载方式。可设置启动时检查更新，也可自动在后台下载程序更新。文件校验完成后，结束当前任务并点击“重启并安装更新”。

程序更新下载较小的更新包，保留配置、任务、平台登录及已安装模型。文件替换失败会恢复原程序。涉及基础运行库变化的版本，需要下载完整便携包。

`release-assets.json` 提供版本信息与校验值。v0.3.0 已提供程序更新包，适用于基础运行库相同的安装目录；v0.2.0 需下载完整版。

## 发布准备

在“发布准备”中选择来源任务和成品视频，核对原视频封面、标题与来源署名，生成发布资料后登录并上传。助手保留创作页面，供用户检查并手动发布。

原封面从视频资料中获取；视频截图和自选图片是独立选项。封面比例转换在需要时补边，保留完整图片。

## 环境与故障排查

- Windows 10／11 x64，建议 16 GB 内存。
- 下载、合并和解压建议预留约 25 GB；可选组件与素材需要额外空间。
- 保留程序旁的 `dependencies`、`models`、`assets` 和 `components`。迁移时复制完整目录。
- 启动错误见 `launcher.log`；诊断工具位于 `tools/`。
- 在新电脑使用时，需重新登录发布平台。

| 诊断工具 | 用途 |
| --- | --- |
| `tools/Start-Diagnostics.bat` | 显示启动诊断信息，并安装缺失运行库 |
| `tools/Check-Dependencies.bat` | 校验内置运行库 |
| `tools/Use-CPU.bat`、`tools/Use-GPU.bat` | 切换基础流程的 CPU／DirectML 设置 |

## 开发

仓库包含源码、测试与构建工具，模型及大型运行库单独分发。源码目录可通过 `tools/Start-Diagnostics.bat` 安装运行库，再运行 `tools/build_windows_launcher.py` 构建 EXE。

```powershell
& '.\dependencies\vocal2midi\python\python.exe' -B .\tools\build_windows_launcher.py
& '.\dependencies\vocal2midi\python\python.exe' -B .\tests\run_tests.py
& '.\dependencies\vocal2midi\python\python.exe' -B .\pipeline.py --audio-file 'D:\Music\song.wav' --zh-lyric-mode pinyin
```

`cache/`、`jobs/` 和 `publish-packages/` 中的用户数据不进入干净发行包和 Git。详见[贡献说明](CONTRIBUTING.md)、[更新记录](CHANGELOG.md)、[使用指南](docs/UserGuide.zh-CN.md)和[结构说明](docs/Architecture.zh-CN.md)。正式打包前需通过自动检查和用户测试确认。

## 相关项目与致谢


助手集成了已有工具和模型流程，感谢各项目的作者与贡献者。

| 项目 | 在助手中的用途 |
| --- | --- |
| [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi) | 通过内置应用层提取音符、对齐歌词并输出 MIDI；仓库许可为 Apache-2.0 |
| [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) 与 [UVR 模型仓库](https://github.com/TRvlvr/model_repo) | MDX 推理参考、分离权重及参数；无需运行 UVR 桌面软件 |
| [Pymss](https://github.com/pymss-project/pymss) / [Pymss Studio](https://github.com/pymss-project/pymss-studio) | 可选 BS-RoFormer 推理；Studio 的侧栏、卡片与操作分组作为界面设计参考 |
| [becruily/bs-roformer-karaoke](https://huggingface.co/becruily/bs-roformer-karaoke) | 可选原始权重与配套配置 |
| [PyTorch](https://pytorch.org/) | 可选组件的 CPU / CUDA 推理运行库 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | B站媒体下载 |
| [FFmpeg](https://ffmpeg.org/) / [Jellyfin FFmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) | 音频解码、媒体检测、视频截图 |
| [Playwright for Python](https://github.com/microsoft/playwright-python) | 浏览器登录及创作页面操作 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | CPU / DirectML 推理 |
| [PyQt](https://www.riverbankcomputing.com/software/pyqt/) | 桌面界面 |
| [NumPy](https://numpy.org/)、[SciPy](https://scipy.org/)、[librosa](https://librosa.org/)、[SoundFile](https://github.com/bastibe/python-soundfile)、[Mido](https://github.com/mido/mido)、[Pillow](https://python-pillow.org/) | 音频、数值、MIDI 和图像处理 |

Vocal2Midi 流程还使用 [GAME](https://github.com/openvpi/GAME)、[HubertFA](https://github.com/wolfgitpr/HubertFA)、[LyricFA](https://github.com/wolfgitpr/LyricFA)、[llama.cpp](https://github.com/ggml-org/llama.cpp) 及其语音、音高模型。详细致谢保留于[上游说明](dependencies/vocal2midi/ACKNOWLEDGEMENTS.md)，组件授权见[第三方说明](THIRD_PARTY_NOTICES.zh-CN.md)。各项目与模型保留独立许可证，此列表不表示整个包采用同一授权。
