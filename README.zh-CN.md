<p align="center">
  <img src="assets/app.svg" width="84" alt="VWA 图标">
</p>

<h1 align="center">VWA · 术力口工作流助手</h1>

<p align="center">
  <strong>面向 Windows 的全链路歌声合成桌面工作台</strong><br>
  串联歌曲准备 · 逐句调音 · 多轨合唱修音 · 12 格式工程互转 · 16 轨原生混音 · 视频合成与投稿资料整理
</p>

<p align="center">
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest">
    <img src="https://img.shields.io/github/v/release/Transilent/Better-Vocaloid-Workflow-Assistant?label=Release&color=3b6ce8" alt="Latest Release">
  </a>
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/actions/workflows/ci.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/Transilent/Better-Vocaloid-Workflow-Assistant/ci.yml?branch=main&label=CI" alt="CI Status">
  </a>
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/actions/workflows/codeql.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/Transilent/Better-Vocaloid-Workflow-Assistant/codeql.yml?branch=main&label=CodeQL" alt="CodeQL">
  </a>
  <img src="https://img.shields.io/badge/Platform-Windows%2010%2F11%20x64-0078D6" alt="Windows x64">
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/Transilent/Better-Vocaloid-Workflow-Assistant?color=43c58e" alt="MIT License">
  </a>
  <img src="https://img.shields.io/badge/QQ%E4%BA%A4%E6%B5%81%E7%BE%A4-490619616-12B7F5" alt="QQ Group 490619616">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <strong>简体中文</strong> ·
  <a href="README.ja.md">日本語</a>
</p>

<p align="center">
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest"><strong>立即下载 v3.0.0</strong></a> ·
  <a href="docs/UserGuide.zh-CN.md">使用指南</a> ·
  <a href="docs/ChorusWorkspace.zh-CN.md">合唱修音工作台</a> ·
  <a href="CHANGELOG.md">更新日志</a> ·
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose">问题反馈</a>
</p>

---

## 目录

- [v3.0.0 核心更新](#v300-核心更新)
- [功能与工作区一览](#功能与工作区一览)
- [下载与安装](#下载与安装)
- [旧版本升级](#旧版本升级)
- [快速上手](#快速上手)
- [硬件与 GPU 加速支持](#硬件与-gpu-加速支持)
- [文档导航](#文档导航)
- [社区支持与反馈](#社区支持与反馈)
- [项目致谢与许可证](#项目致谢与许可证)

---

## v3.0.0 核心更新

- **全新修音与合唱工作台（Chorus Workbench）**：多轨人声干声与 MIDI 对照修音、逐句复核、精准手绘音高与音节力度调节、Signalsmith Stretch 原生高保真连续变调（8 kHz tonality / 共振峰保留）、CAS 缓存与分轨干声交付。
- **独立工程格式安全互转页（Project Converter）**：支持 **12 种**歌声合成工程（SVP / SV2、USTX、VPR、VSQX、ACEP、UFData、MIDI 等）互转、轨拆分、回读体检与时间轴 LRC/SRT 歌词导出，绝不覆盖源文件。
- **16 轨原生实时混音引擎**：新增 WASAPI 音频设备枚举与热插拔自动重连、原生压限/输出引擎、60fps FBO 波形与草稿崩溃恢复。
- **BS-RoFormer 双运行库共存 + 实验性 AMD WebGPU ONNX**：原版 PyTorch BS-RoFormer 支持 CUDA 与 CPU 独立共存及权重复用；新增默认关闭的独立实验组件 **BS-RoFormer ONNX（10s FP16 / 20s FP16）**（致谢 `@AU-ki`），隔离运行 `onnxruntime-webgpu 1.27.0`，权重已镜像托管至主仓库。
- **零依赖在线引导安装器 + 全版本二跳自动迁移**：新增 139 KB 单文件 `VWA-Online-Installer-3.0.0-x64.exe`（三语、深浅主题、Full/JPN 卡片自选、镜像竞速与多线程续传）；旧版用户可在应用内通过二跳本地 Web UI 迁移器无损升级至 3.0.0。
- **效率与体验升级**：4 场景新手引导、全局快捷命令（`Ctrl+K`）、快捷键速查（`Ctrl+/`）、窗口级拖拽路由、批量准备队列、组件状态清晰标识，以及 10 条安全告警全部清零。

---

## 功能与工作区一览

| 工作区 | 核心能力 |
| --- | --- |
| **准备工作流** | 导入本地音频或 B站 BV 号／链接（支持批量队列），分离主唱、和声与伴奏，识别中／日／英歌词与音符，导出 MIDI、USTX、VSQX 或 SVP，支持手动指定 BPM 与连续音高线。 |
| **调音工作流** | 打开任务工程或导入 SVP／SV2（支持 `version: 203+` 独立音符组）、USTX、VPR、VSQX、ACEP、UFData；支持按歌词定位、音符与词编修、手绘音高、参考人声映射、实测声库校准与戏腔实验层，另存独立工程不覆写源文件。 |
| **修音与合唱工作台** | 面向多轨人声干声的修音与合唱对齐：对照参考 MIDI 逐句复核、手绘音高、音节力度调节、原生高保真连续变调试听与分轨干声交付。详见[合唱工作台说明](docs/ChorusWorkspace.zh-CN.md)。 |
| **工程格式互转** | 独立安全转换 12 种歌声合成工程格式，提供轨拆分、转换后回读体检与时间轴 LRC／SRT 歌词导出。 |
| **简易混音** | 最高 16 轨原生实时 PCM 混音台，支持音量、声像、静音／独奏、时间对齐、独立混响返回、共享人声总线 EQ／去齿音／压限、A/B 响度匹配试听与 WASAPI 设备热插拔恢复。 |
| **合成视频** | 将原视频与导出 Mixdown 合成，支持自动对齐、手动偏移与可选响度处理。 |
| **发布** | 整理封面、标题、简介、STAFF 署名与平台文案；B站支持辅助上传，小红书提供本地发布包、一键复制与 AI 内容声明核对，由用户在官方端手动确认发布。 |
| **任务与结果** | SQLite 任务索引支持搜索、筛选、断点续跑、参数重做、文件血缘校验、日志与本机诊断摘要导出。 |
| **模型与组件** | 统一管理中文／英文 Qwen3-ASR、BS-RoFormer（PyTorch CUDA／CPU 双共存 + 实验性 ONNX WebGPU）与 Pedalboard，支持多镜像源竞速、SHA-256 完整校验与实时进度。 |
| **设置与支持** | 浅色／深色主题、月光玻璃／纯色材质、中／英／日界面语言、界面缩放、[本机预设库](docs/PresetLibrary.zh-CN.md)、缓存扫描清理与在线更新管理。 |
| **Agent／MCP（可选）** | 独立可选安装的技术预览，向兼容宿主暴露 12 个具名顶层工具，覆盖准备、证据审阅、决策与交付，不提供任意命令执行器。详见[MCP 12 工具说明](docs/AgentMCPStandard.zh-CN.md)。 |

> **效率快捷键**：在桌面窗口任意位置按 `Ctrl+K` 唤出全局快捷命令面板，按 `Ctrl+/` 查看完整快捷键速查；直接拖入音频、视频或工程文件即可自动路由至对应工作区。

---

## 下载与安装

前往 [**VWA v3.0.0 正式发布页**](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest) 选择适合你的安装方式（均原生支持简体中文、英语、日语界面，界面语言与歌词识别语言分开设置）：

| 方式 | 推荐人群 | 下载内容 | 特点 |
| --- | --- | --- | --- |
| **⭐ 在线引导安装器（推荐）** | 新用户 & 任意旧版本覆盖升级 | `VWA-Online-Installer-3.0.0-x64.exe`（约 139 KB） | Windows 10/11 零依赖单文件；图形界面自选 **Full / JPN** 与安装目录，自动国内镜像竞速、多线程断点续传与 SHA-256 校验，覆盖旧目录时自动无损保留个人数据与模型。 |
| **绿色便携版 · Full 完整版** | 离线保存 / 手动部署全模型 | `VWA-Windows-x64.zip.001`～`.003` + `portable-metadata-full.json` + `Merge-PortableParts.ps1`（合并后约 4.93 GiB） | 开箱包含中文／英文 Qwen3-ASR 与全部基础运行库，解压即用，移动文件夹不依赖注册表。 |
| **绿色便携版 · JPN 精简版** | 仅需日语／纯音符或希望轻快下载 | `VWA-Windows-x64-JPN.zip`（约 1.55 GiB，单文件） | 仅省略三份中文 Qwen3-ASR 权重，所有工作台与日语识别完全保留；后续可随时在「模型与组件」页按需补装中文／英文模型。 |

> **注意**：GitHub 自动生成的 **Source code** 源码包，以及 `VWA-Program-Update.zip`、`VWA-App-Update-*.zip`、`VWA-Runtime-Upgrade-*.zip`、`BVWA-App-Update.zip` 等更新桥接包均**不是**完整安装包，请勿手工解压覆盖。

### 方式一：使用在线引导安装器（最省心）

1. 下载并双击运行 **`VWA-Online-Installer-3.0.0-x64.exe`**。
2. 选择 **Full 完整版** 或 **JPN 精简版**，指定安装目录（建议较短可写路径如 `D:\Apps\VWA`；若指向已有旧版 VWA 目录会自动提示无损保留数据）。
3. 点击**开始下载并安装**，等待自动竞速下载、校验并解压完成即可启动。

### 方式二：手动使用绿色便携版

- **JPN 精简版**：下载 `VWA-Windows-x64-JPN.zip` 直接完整解压，进入 `VWA` 文件夹双击 **`VWA.exe`**。
- **Full 完整版**：将 `VWA-Windows-x64.zip.001`、`.002`、`.003`、`portable-metadata-full.json`、`Merge-PortableParts.ps1` 放在同一文件夹，在该目录打开 PowerShell 运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Merge-PortableParts.ps1
```

脚本校验全部三卷并生成 `VWA-Windows-x64.zip`，完整解压后进入 `VWA` 文件夹运行 **`VWA.exe`**。所有发布附件均可通过 `SHA256SUMS.txt` 核对完整性。

---

## 旧版本升级

| 当前已安装版本 | 推荐升级方式 | 个人数据与模型保留 |
| --- | --- | --- |
| **`v2.0.0` / `v2.1.0`** | 在应用内**设置与支持 → 检查更新**，按提示重启进入二跳本地 Web UI 全量迁移器，自动下载 3.0.0 并迁移数据后启动新版。 | 自动迁移 `config.json`、`jobs/`、`user-presets/`、`cache/accounts/`、`publish-packages/`、`dependencies/optional/` 与已下载 Qwen 权重；原目录完整保留便于回退。 |
| **`v1.0.0` / `v1.1.0`** | 在应用内**设置与更新 → 检查更新**，通过两段式桥接自动进入 3.0.0 本地 Web UI 迁移器完成全量迁移。 | 同上，全部用户数据与后装模型无损迁入新目录。 |
| **`v0.4.0`** | 支持在应用内检查更新进入 3.0.0 二跳迁移器，或直接使用在线引导安装器。 | 同上。 |
| **`v0.3.x` / `v0.4.1`** | 下载 **`VWA-Online-Installer-3.0.0-x64.exe`**，将安装目录指向现有旧版文件夹执行无损覆盖升级（或装到新目录后复制 `jobs/`、`config.json`、`user-presets/`）。 | 覆盖升级自动保留个人设置、任务、预设、账号会话与已下载模型。 |

> 升级前请先关闭正在运行的 VWA，并备份仍在外部声库编辑器中打开的工程文件。

---

## 快速上手

1. **歌曲准备**：在**准备工作流**拖入本地歌曲或粘贴 B站 BV 号／链接，选择分离方式、歌词语言与输出目录，点击开始即可生成分轨音频、MIDI 与编辑器工程。
2. **调音与互转**：在**调音工作流**打开任务工程或本地 SVP／SV2／USTX／VPR／VSQX／ACEP／UFData，按歌词定位并编辑音符、风格或音高曲线，另存候选后在对应声库编辑器中合成干声；需要跨平台转换工程时，直接使用**工程格式互转**。
3. **修音与混音**：在**修音与合唱工作台**对多轨人声干声逐句精修，再进入**简易混音**（最高 16 轨）完成音量、EQ、去齿音、压缩与混响实时试听并导出 Mixdown。
4. **合成视频与发布**：在**合成视频**中对齐原视频与 Mixdown，最后在**发布**页整理封面、文案与署名，在平台页面核对后手动发布。

---

## 硬件与 GPU 加速支持

- **运行环境**：Windows 10 / 11 x64，便携包与在线安装器均自带独立 Python 与 Qt 6.10.3 运行库，无需本机预装 Python。声库合成需自行准备对应编辑器与声库。
- **显卡与后端建议**：

| 显卡类型 | 推荐推理后端 | 说明 |
| --- | --- | --- |
| **NVIDIA 独立显卡** | 基础 DirectML + 可选 **BS-RoFormer（PyTorch CUDA）** | 分离、音高与 ASR 均可获得最佳速度；DirectML 与 CUDA 可按引擎独立选择。 |
| **AMD / Intel 显卡与核显** | 基础 DirectML + 可选 **BS-RoFormer ONNX（实验性 WebGPU）** | 默认关闭，可在「模型与组件」按需开启隔离运行库：`≤8 GB` 显存/核显推荐 `10s FP16`，`≥12 GB` 显存可选 `20s FP16`。 |
| **纯 CPU / 驱动重置回退** | CPU 模式 | 遇到 DirectML 显卡重置（如 `0x887A0005`）时自动安全回退 CPU 继续完成任务，亦可随时手动切换。 |

---

## 文档导航

| 分类 | 文档 |
| --- | --- |
| **新手与日常使用** | [完整使用指南](docs/UserGuide.zh-CN.md) · [便携版与安装说明](docs/Portable.zh-CN.md) · [启动引导与教程](docs/StartupExperience.zh-CN.md) · [本机预设库](docs/PresetLibrary.zh-CN.md) |
| **核心工作流专题** | [调音工作流与工程边界](docs/TuningWorkflow.zh-CN.md) · [修音与合唱工作台](docs/ChorusWorkspace.zh-CN.md) · [简易混音组件](docs/SimpleMixingComponent.zh-CN.md) · [实时试听说明](docs/RealtimeAudition.zh-CN.md) · [风格预设与戏腔](docs/StylePresets.zh-CN.md) |
| **模型、网络与更新** | [下载来源与镜像加速](docs/DownloadSources.zh-CN.md) · [AMD 显卡与 WebGPU 适配](docs/AmdGpuAdaptation.zh-CN.md) · [更新协议与兼容性](docs/UpdateProtocol.zh-CN.md) · [界面语言说明](docs/InterfaceLanguages.zh-CN.md) |
| **进阶与开发** | [Agent / MCP 12 工具规范](docs/AgentMCPStandard.zh-CN.md) · [系统架构](docs/Architecture.zh-CN.md) · [开发者指南](docs/DeveloperGuide.zh-CN.md) · [贡献规范](CONTRIBUTING.zh-CN.md) · [安全策略](SECURITY.md) |

---

## 社区支持与反馈

- **用户交流群：QQ 群 `490619616`**（日常使用交流、经验分享与简短答疑）
- **问题反馈与功能建议**：前往 [GitHub Issues](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose)（提交前建议先在应用内点击 **设置与支持 → 本机诊断 → 复制摘要**，并按[支持说明](.github/SUPPORT.md)隐藏个人路径与隐私信息）

---

## 项目致谢与许可证

感谢以下开源项目、模型作者与社区贡献者：

| 项目 / 贡献者 | 在 VWA 中的用途 |
| --- | --- |
| [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi) | 音符识别与歌词对齐。 |
| [LibreSVIP](https://github.com/SoulMelody/LibreSVIP) | 多格式歌声合成工程读写与互转。 |
| [Signalsmith Stretch](https://github.com/Signalsmith-Audio/signalsmith-stretch) | 合唱修音工作台原生高保真连续变调与共振峰保留。 |
| [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui)／[UVR 模型仓库](https://github.com/TRvlvr/model_repo) | 人声分离、模型权重与参数。 |
| [Pymss](https://github.com/pymss-project/pymss)／[becruily BS-RoFormer karaoke](https://huggingface.co/becruily/bs-roformer-karaoke) | 可选主唱／和声分离引擎与模型。 |
| [@AU-ki](https://github.com/AU-ki) | BS-RoFormer ONNX 导出脚本（`tools/export_roformer_onnx.py`）与跨厂商 WebGPU 验证（RX 9070 XT、Arc 130V、RTX 2080Ti 等）。 |
| [Pymss Studio](https://github.com/pymss-project/pymss-studio) | 界面及模型管理设计参考。 |
| [PyWORLD](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder)／[WORLD](https://github.com/mmorise/World) | 参考演唱声学分析。 |
| [163MusicLyrics](https://github.com/jitwxs/163MusicLyrics) | 歌词查询行为参考。 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 媒体下载。 |
| [FFmpeg](https://ffmpeg.org/)／[Jellyfin FFmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) | 音视频处理。 |
| [Playwright for Python](https://github.com/microsoft/playwright-python) | 浏览器登录与上传辅助。 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime)／[PyTorch](https://pytorch.org/) | 模型推理运行库。 |
| [Qt Quick 与 PySide6](https://doc.qt.io/qtforpython-6.10/) | 桌面界面框架。 |
| [NumPy](https://numpy.org/)、[SciPy](https://scipy.org/)、[librosa](https://librosa.org/)、[SoundFile](https://github.com/bastibe/python-soundfile)、[Mido](https://github.com/mido/mido)、[Pillow](https://python-pillow.org/) | 数值、音频、MIDI 与图像处理。 |

所集成的 Vocal2Midi 流程还使用 [GAME](https://github.com/openvpi/GAME)、[HubertFA](https://github.com/wolfgitpr/HubertFA)、[LyricFA](https://github.com/wolfgitpr/LyricFA)、[RomajiASR](https://github.com/Xiantaidu/RomajiASR)、[llama.cpp](https://github.com/ggml-org/llama.cpp) 及相关语音和音高模型，更多致谢见[上游说明](dependencies/vocal2midi2/ACKNOWLEDGEMENTS.md)。

- **许可证**：VWA 原创代码采用 [MIT 许可证](LICENSE)。第三方代码、运行库与模型权重分别保留其原有许可证与声明，详见[第三方说明](THIRD_PARTY_NOTICES.zh-CN.md)与 [v3.0.0 运行库许可及对应源码说明](docs/ThirdPartyRuntime.md)。
