<p align="center"><img src="assets/app.svg" width="80" alt="VWA 图标"></p>

# VWA（术力口工作流助手）

Vocaloid Workflow Assistant (VWA)

[English](README.md) | [简体中文](README.zh-CN.md)

面向 Windows 的声库调音工作流助手。从本地音乐或 B站视频开始，分离人声与伴奏，生成带歌词的 MIDI；调音完成后，准备成品视频的封面、简介和平台上传。

按**准备工作流 → 调音工作流 → 合成视频 → 发布**完成制作。[月光工作空间](docs/Frontend.zh-CN.md)支持浅色与深色、玻璃与纯色材质及界面缩放，启动入口为 **`VWA.exe`**。


## 功能

- 可展开的工作流卡片、处理预设与各引擎 CPU / GPU 设备设置。
- 任务搜索、筛选、排序和管理，分阶段进度、错误恢复与缓存清理。
- 网易云时间轴或 MIDI 歌词事件匹配字幕，支持声部选择与逐行校准。

- 支持 B站 BV 号／链接和本地音频。
- 分离人声与伴奏，分别生成主唱和和声 MIDI。
- 内置 Karaoke 2，可按需安装支持 CPU／NVIDIA GPU 的 BS-RoFormer。
- 支持中文和日语歌词；中文 MIDI 可选择汉字或拼音。
- 支持任务续跑、参考歌词和完整 WAV 分轨导入。
- 获取原视频封面，编辑来源署名，准备 B站和小红书上传。
- 提供无控制台窗口的 `VWA.exe` 启动器及经过校验的程序更新。

调音工作流生成独立 SVP 候选，你在声库编辑器中合成歌声后，可将 Mixdown 对齐到视频；已有成品可直接进入发布。使用结果前请核对音频、音符和歌词，最终发布在平台页面手动确认。

视频合成与原曲独立提取主唱见[可选工作流](docs/OptionalWorkflows.zh-CN.md)。[声库校准](docs/ReferenceDynamics.zh-CN.md#声库响应校准拟合开发实验)需要对应目标声库的实测响应。

[参考演唱参数](docs/ReferenceDynamics.zh-CN.md)可为 SVP 添加可编辑的力度、张力、气声和发声强度曲线。参考映射是可选实验功能，音色分析需要安装 WORLD 组件。

[参考演唱参数调研](docs/research/SVExpression.zh-CN.md)记录了调音教程、公开数据集，以及将声学测量映射到 SV 参数时的限制。

[音符与连续音高](docs/TranscriptionQuality.zh-CN.md)说明音节边界、参考歌词对齐和实测音高导出；识别结果仍需核对。

## 快速开始

1. 在[发行页](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases)下载完整便携包：全量包为 `VWA-Windows-x64.zip`，JPN 包为 `VWA-Windows-x64-JPN.zip`。JPN 包不含中文 Qwen 权重。GitHub 的 **Source code** 不含运行库。
2. 如提供分卷，请将全部分卷与对应的 `portable-metadata-full.json` 或 `portable-metadata-jpn.json` 放在同一目录，并使用该包附带的合并工具。
3. 解压到可写目录。解压后的顶层文件夹为 `VWA`，请保留其中全部内容。
4. 进入 `VWA`，双击 **`VWA.exe`**。
5. 在准备工作流添加音乐，核对歌词、音符及输出，点击开始准备。
6. 在**设置与更新 → 外观 → 界面语言**选择简体中文、日本語或 English，下次启动生效。

## 升级

在**设置与更新**检查更新，下载匹配的更新后重启安装。可用升级路径以对应发行说明为准。助手复用已安装模型，保留设置、任务、登录和可选组件；下载支持备用源、校验、续传与失败回退。旧版需要兼容工具时，请参阅[升级说明](docs/UpdateProtocol.zh-CN.md)。

已有安装可按发行说明升级，或在提供兼容工具时运行 `VWA-Updater.exe`；更新包不能作为完整安装包。

首次安装请下载完整便携包。升级前请备份正在编辑的工程。GitHub 的 Source code 和单独的更新 ZIP 均不能作为完整安装包使用。

## 保存目录

在“音频导出 → 保存目录”点击“选择目录”，或输入新的文件夹路径。保存目录用于整个任务，关闭音频导出时也生效。默认使用助手目录的 `jobs` 文件夹。每个任务会新建独立子文件夹，保存下载的视频（BV 输入为 `video/source.mp4`）、音频和 MIDI。助手会记住所选目录及以前的任务位置；继续任务沿用原目录，重新处理则在当前选定目录中新建任务。

## 歌词与输出

新任务默认使用 Vocal2Midi 2.0，无需选择提取后端。旧任务继续使用其记录的处理设置。

选择中文后，“中文歌词格式”提供“汉字”和“拼音”。此设置同时用于主唱与和声，并随任务保存。关闭“识别歌词并写入 MIDI”时只导出音符。中文格式选项不影响日语识别。

参考歌词可选填，主唱与和声分别填写，只保留演唱文本。完成后在“任务与结果”查看文件。

| 文件 | 内容 |
| --- | --- |
| `midi/voices.mid` | 时间对齐的主唱、和声双轨 MIDI |
| `midi/lead/lead.mid`、`midi/backing/backing.mid` | 单独声部 MIDI |
| `midi/vocals.mid` | 合并人声模式的结果 |
| `audio/lead.wav`、`audio/backing.wav` | 主唱、和声分离音频 |
| `audio/instrumental.wav` | 伴奏 |

VOCALOID 6 请通过“文件 → 导入”载入 MIDI，提供歌词编码选项时选择 UTF-8。

在 **MIDI 与工程 → 手动指定 BPM** 中设置 20–400 之间的固定速度，支持小数。MIDI、USTX、VSQX 和 SVP 会使用该速度，同时保留音符、音高线和伴奏的实际时间。设置随预设和新任务保存。关闭时沿用 120 BPM 时间基准，不自动检测歌曲速度，也不拉伸音频。变速歌曲暂不支持逐段设置速度。继续旧任务沿用原任务 BPM；需要修改时，使用“用当前工作流重做”。

## 可选模型与下载源

在“模型与组件”选择运行库和下载方式，点击“下载并安装”；安装完成后选择“使用此模型”。

| 运行库 | 下载量 | 安装后约占用 |
| --- | --- | --- |
| CPU | 0.46 GB | 1.56 GB |
| NVIDIA GPU / CUDA | 3.52 GB | 6.14 GB |

安装支持取消、续传和 SHA-256 校验。中日文使用同一份原始 BS-RoFormer 权重；日语识别保留现有 Romaji ASR。

打开**模型与组件 → 中文歌词识别**，未预装时点击**下载中文模型**；已有文件可用**校验 / 修复**检查，只下载缺失或损坏部分。原始 Qwen 权重约占 4.38 GiB，日语和纯音符提取可独立运行。显示语言单独在设置中选择，见[界面语言](docs/InterfaceLanguages.zh-CN.md)。

下载支持自动、直连、优先加速及指定服务。GitHub 文件可选 GitProxy、GH-Proxy、ghfast；Hugging Face 可选 HF-Mirror；部分固定运行库文件可选清华、阿里云及上海交大镜像。适用范围见[下载源](docs/DownloadSources.zh-CN.md)，速度和可用性取决于网络与所选服务。

## 工作流、任务与恢复

准备工作流集中显示音乐来源、处理方式、歌词与音符、输出内容，按需展开歌词和音符选项。顶部设备设置选择各引擎的设备。预设保存处理选项，不包含歌曲路径或参考歌词。

工作流依次是**准备、调音、合成视频、发布**。调音可选择任务工程或导入 SVP，生成独立可恢复候选；选择一种主参数来源，再按需加入风格、音高变化和曲线加密。强度可按 1% 拖动或直接输入数值。新准备任务将表现处理留给调音，旧任务沿用原设置。格式与恢复方式见[调音工作流](docs/TuningWorkflow.zh-CN.md)。设置提供浅色／深色、月光玻璃／纯色阅读及界面缩放。

“设备设置”还提供共用的“DirectML 显卡”和“CPU 线程数”。自动模式优先选择独立显卡，并保留各引擎的默认线程设置。指定线程数会限制相关 CPU 运算的线程数量；线程更多不一定更快。这些选项随预设保存。

取消 MIDI 与工程即可只处理音频；编辑器工程也可单独关闭，仍保留标准 MIDI。工程支持 USTX、VSQX、SVP，可保留 RMVPE 音高线并引用伴奏。音频支持 WAV、FLAC、MP3、OGG，WAV 直接使用任务音频。合成歌声需要在编辑器中渲染。

分轨峰值超出音频范围时，FLAC、MP3、OGG 会共享必要的峰值衰减，保持人声与伴奏之间的音量关系。增益记录在 `exports/report.json` 中，原始浮点 WAV 保留完整信号。

**任务与结果**支持搜索、筛选、重命名、查看日志和重试。继续任务使用其记录的参数；**用当前工作流重做**使用当前预设创建新任务。进度显示已完成阶段、耗时和可测量的子任务。复用结果前会校验文件，设备错误可改用 CPU 继续。

## 缓存管理与歌词字幕

导出文件与记录的内容不同时，重新生成前会将原文件保存到任务的 `backups/` 文件夹，并在报告中记录位置。直接修改 MIDI 或工程后也能保留编辑内容；未修改的正常结果不会额外备份。建议把正在调音的工程另存为工作文件。

在**设置与更新 → 缓存与磁盘**中扫描占用，选择可再生成的缓存后清理。任务结果、模型、预设和登录信息会保留。下载及未完成的分段缓存可在相关操作结束后清理。

WAV 导出直接使用任务中已有的音频，压缩格式另行保存。继续任务时会校验输入与输出；损坏的导出文件逐个修复。发布资料保留成品视频的路径与哈希，不重复复制完整视频。上传前请保留成品文件，移动或修改后重新生成发布资料。

**参考歌词与校准**卡片位于分离声部之后。选择**网易云匹配**，可搜索歌名和歌手或粘贴歌曲 ID／链接，选中的原文歌词默认填入主唱；选择**本地匹配**，可导入 LRC／YRC／TXT 歌词或直接填写，留空时从对应人声音频本地识别。两个来源分别保留主唱文本，网易云匹配不会覆盖和声歌词。参考文本会与人声音频对齐，歌曲时间戳不会直接移动 MIDI 音符。

**歌词转字幕**卡片分为**网易云匹配**与 **MIDI 匹配**：前者使用歌曲已有的歌词时间轴，后者使用 MIDI 歌词事件与速度变化。导入多轨 MIDI 后可选择声部，两个来源各自保留编辑内容。核对文字、时间和整体偏移后导出 UTF-8 SRT／WebVTT。没有时间轴的纯文本不能直接生成字幕，不会按总时长均分。字幕匹配不覆盖音乐提取使用的参考歌词。

## 程序更新

“设置与更新”提供版本检查、变更说明和默认下载方式。默认仅在启动时自动检查、下载并安装更新。启动更新校验完成后，显示 10 秒倒计时，重启安装并重新打开助手；可选择“暂缓安装”继续使用当前版本。开始使用助手后，自动安装延至下一次启动。使用过程中不会定时检查更新；手动下载的更新需要点击“重启并安装更新”。

程序更新下载与现有运行库匹配的更新包，保留配置、任务、平台登录及已安装模型。文件替换失败会恢复原程序。发布版提供运行库增量升级时，助手会在重启后下载并安装对应文件。

旧版 BVWA 客户端使用兼容路径时，可按发行说明运行 `VWA-Updater.exe`；具体方式见[旧版兼容说明](docs/UpdateProtocol.zh-CN.md)。

发行目录中的 `portable_downloads.full` 与 `portable_downloads.jpn` 标识当前 VWA 包。请使用适合当前安装目录的包；更新包不能替代完整便携包。

## 发布工作流

**发布**位于可选的**合成视频**页面之后，每次显示一个完整步骤：

1. **成品与来源**：选择成品视频和来源任务，或通过 BV 读取本家资料。
2. **视频封面**：大图预览本家原始封面，也可选择图片或视频画面。本地音乐任务需要自选图片，或另行读取 BV 来源。
3. **标题与简介**：同时编辑两平台文案，应用标题格式与署名，并核对字数和 STAFF。
4. **账号与上传**：点击“生成发布资料”，登录后点击“上传所选平台”。主按钮固定在底部，滚动内容时仍可操作。

底部的上一步、下一步和步骤编号用于切换页面；点击下一步时会检查当前必填内容。各平台分别提供登录、继续填写和关闭任务入口，不可用的操作会禁用。登录完成后关闭登录窗口；上传后保留创作页面，由用户检查并手动发布。

本家封面从视频资料读取，截图与自选图片是独立选项。适配 4:3 和 16:9 比例时按需补边，保留完整原图。

修改成品、封面或文案后，需要重新生成发布资料。“继续填写当前上传”可更新仍打开且视频相同的上传页面。浏览器关闭后需重新上传。发布前请在创作页面核对分区、标签、署名和其他必填项。

![发布工作流封面预览](docs/images/publishing-cover.png)

## 环境与故障排查

B站简介、引用来源、原封面和媒体下载使用直连，不继承系统代理或代理环境变量。组件下载和更新检查直接连接所选的官方源或加速源；运行库安装及助手的专用登录与上传浏览器也使用直连。读取失败时，请检查当前网络是否能访问来源，再重试或切换下载源。

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

应用源码位于 `src/`，配置模板与构建清单位于 `packaging/`；测试、工具和文档各自存放在对应目录。模型及大型运行库单独分发。

从源码启动时，`tools/Start-Diagnostics.bat` 会准备开发运行目录并安装缺失运行库。请修改 `src/` 中的代码，并在测试或构建前执行下面的准备命令。该命令只复制开发文件，不修改用户设置或项目数据。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Prepare-Source.ps1
& '.\dependencies\vocal2midi\python\python.exe' -B .\tools\build_windows_launcher.py
& '.\dependencies\vocal2midi\python\python.exe' -B .\tests\run_tests.py
& '.\dependencies\vocal2midi\python\python.exe' -B .\pipeline.py --audio-file 'D:\Music\song.wav' --zh-lyric-mode pinyin
```

`cache/`、`jobs/` 和 `publish-packages/` 中的用户数据不进入干净发行包和 Git。详见[贡献说明](CONTRIBUTING.md)、[更新记录](CHANGELOG.md)、[使用指南](docs/UserGuide.zh-CN.md)和[结构说明](docs/Architecture.zh-CN.md)。正式打包前需通过自动检查和用户测试确认。

## 许可证

助手原创代码采用 [MIT 许可证](LICENSE)。随附运行库与模型各自保留原有许可证，详见[第三方声明](THIRD_PARTY_NOTICES.zh-CN.md)。

组件与更新的大文件下载在服务端支持 HTTP Range 时使用最多 8 个并发连接，可续传并校验 SHA-256；不支持的服务端使用单连接。上传 GitHub 直接使用官方端点。

## 相关项目与致谢


助手集成了已有工具和模型流程，感谢各项目的作者与贡献者。

| 项目 | 在助手中的用途 |
| --- | --- |
| [LibreSVIP](https://github.com/SoulMelody/LibreSVIP) | USTX、VSQX、SVP 工程转换；MIT |
| [163MusicLyrics](https://github.com/jitwxs/163MusicLyrics) | 歌词查询行为参考，未附带其桌面程序 |
| [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi) | 通过内置应用层提取音符、对齐歌词并输出 MIDI；仓库许可为 Apache-2.0 |
| [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) 与 [UVR 模型仓库](https://github.com/TRvlvr/model_repo) | MDX 推理参考、分离权重及参数；无需运行 UVR 桌面软件 |
| [Pymss](https://github.com/pymss-project/pymss) / [Pymss Studio](https://github.com/pymss-project/pymss-studio) | 可选 BS-RoFormer 推理；Studio 的侧栏、卡片与操作分组作为界面设计参考 |
| [becruily/bs-roformer-karaoke](https://huggingface.co/becruily/bs-roformer-karaoke) | 可选原始权重与配套配置 |
| [PyTorch](https://pytorch.org/) | 可选组件的 CPU / CUDA 推理运行库 |
| [PyWORLD](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder) / [WORLD](https://github.com/mmorise/World) | 参考演唱曲线所需的可选谐波与噪声分析；MIT / BSD-3-Clause |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | B站媒体下载 |
| [FFmpeg](https://ffmpeg.org/) / [Jellyfin FFmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) | 音频解码、媒体检测、视频截图 |
| [Playwright for Python](https://github.com/microsoft/playwright-python) | 浏览器登录及创作页面操作 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | CPU / DirectML 推理 |
| [PyQt](https://www.riverbankcomputing.com/software/pyqt/) | 桌面界面 |
| [NumPy](https://numpy.org/)、[SciPy](https://scipy.org/)、[librosa](https://librosa.org/)、[SoundFile](https://github.com/bastibe/python-soundfile)、[Mido](https://github.com/mido/mido)、[Pillow](https://python-pillow.org/) | 音频、数值、MIDI 和图像处理 |

Vocal2Midi 流程还使用 [GAME](https://github.com/openvpi/GAME)、[HubertFA](https://github.com/wolfgitpr/HubertFA)、[LyricFA](https://github.com/wolfgitpr/LyricFA)、[llama.cpp](https://github.com/ggml-org/llama.cpp) 及其语音、音高模型。详细致谢保留于[上游说明](dependencies/vocal2midi/ACKNOWLEDGEMENTS.md)，组件授权见[第三方说明](THIRD_PARTY_NOTICES.zh-CN.md)。各项目与模型保留独立许可证，此列表不表示整个包采用同一授权。
