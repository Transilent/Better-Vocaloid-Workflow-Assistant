<p align="center"><img src="assets/app.svg" width="80" alt="VWA 图标"></p>

# VWA 3.0.0（术力口工作流助手）

[English](README.md) | [简体中文](README.zh-CN.md)

面向 Windows 10/11 x64 的全链路歌声合成工作流助手，涵盖歌曲准备、逐句调音、多轨合唱修音、12 格式工程安全互转、16 轨原生实时混音、视频合成与投稿资料整理。

## 开始使用

1. **获取方式**：推荐从[发布页](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest)直接下载 **`VWA-Online-Installer-3.0.0-x64.exe`**（在线引导安装器，支持自选 Full / JPN、国内镜像竞速与断点续传），或手动下载 Full 分卷便携包 / JPN 单文件便携包。解压后的顶层文件夹为 `VWA`，进入该文件夹双击 **`VWA.exe`**，并保留程序旁的配套目录。
2. **准备工作流**：拖入本地歌曲或输入 B站 BV／链接（支持批量队列），核对分离、歌词、音符与输出后点击**开始准备**。
3. **任务与结果**：查看音频、MIDI 和工程，支持搜索筛选、断点续跑与按当前预设重做。
4. **调音与工程互转**：在**调音工作流**打开任务工程或导入 SVP／SV2（支持 `version: 203+` 独立音符组）、USTX、VPR、VSQX、ACEP 或 UFData，另存为独立工程；需要跨编辑器转换时，在**工程格式互转**页安全互转 12 种格式并导出时间轴 LRC／SRT。
5. **修音与简易混音**：在**修音与合唱工作台**对多轨人声干声对照参考 MIDI 逐句修音与连续变调试听，再进入**简易混音**（最高 16 轨原生实时引擎，支持 WASAPI 设备热插拔恢复）导出 Mixdown WAV。
6. **合成视频与发布**：按需合成视频，或将已有成品直接进入**发布**整理封面与平台文案，最后在创作页面手动确认发布。

> 按 `Ctrl+K` 唤出全局快捷命令，按 `Ctrl+/` 查看快捷键速查；直接拖入音视频或工程文件可自动跳转对应工作区。

## 选择包型

| 包型 | 文件 | 用途 |
| --- | --- | --- |
| 在线引导安装器（推荐） | `VWA-Online-Installer-3.0.0-x64.exe` | 约 139 KB 零依赖单文件，图形化选择 Full 或 JPN，自动镜像竞速、多线程续传并校验解压；覆盖旧目录时自动保留个人数据与模型。 |
| 全量便携包 (Full) | `VWA-Windows-x64.zip.001`～`.003` + `portable-metadata-full.json` + `Merge-PortableParts.ps1` | 合并后约 4.93 GiB，含中文／英文 Qwen3-ASR 权重与全部基础运行库，解压即用。 |
| 精简便携包 (JPN) | `VWA-Windows-x64-JPN.zip` | 约 1.55 GiB 单文件，仅省略三份中文 Qwen3-ASR 权重，所有工作台与日语识别完整可用；中文／英文模型可在**模型与组件**按需补装。 |

GitHub 自动生成的 Source code 源码包与更新桥接 ZIP（`VWA-Program-Update.zip`、`VWA-App-Update-*.zip`、`VWA-Runtime-Upgrade-*.zip`、`BVWA-App-Update.zip`）均不含完整运行库，不能单独作为完整安装包解压使用。

## 设置、模型与输出

- **外观与语言**：在**设置与支持 → 外观**切换浅色／深色主题、月光玻璃／纯色材质、界面缩放以及简体中文／日本語／English（下次启动生效）。歌词识别语言在准备工作流单独选择。
- **可选分离模型**：在**模型与组件**可安装原版 PyTorch BS-RoFormer（支持 GPU CUDA 与 CPU 双运行库共存及权重复用）；AMD 等非 NVIDIA 显卡还可按需开启默认关闭的实验性 **BS-RoFormer ONNX（WebGPU，10s / 20s FP16）**，运行于隔离的 `onnxruntime-webgpu 1.27.0` 子进程。
- **Agent／MCP（可选）**：独立安装的技术预览，向兼容宿主暴露 12 个具名顶层工具，不提供任意命令执行器。详见 [MCP 12 工具说明](docs/AgentMCPStandard.zh-CN.md)。
- **下载源与缓存**：在设置或组件页选择自动竞速、GitHub 直连或国内加速镜像，详见[下载来源](docs/DownloadSources.zh-CN.md)。缓存清理默认保护任务结果、模型、预设与账号登录信息。用户 QQ 群：`490619616`。

## 简易混音与试听

简易混音已升级至 **16 轨原生实时音频引擎**，支持多条人声与伴奏 WAV 的音量、声像、静音／独奏、时间偏移与独立混响发送，共享人声总线提供 EQ、去齿音、压缩、压限与混响，以及 A/B 响度匹配连续试听。支持 WASAPI 设备枚举、热插拔自动重连、60fps FBO 波形与草稿崩溃恢复。首次使用 Pedalboard 效果时可按界面提示在隔离目录 `dependencies/optional/pedalboard/current` 安装组件。

## 更新与排错

- **旧版本升级至 3.0.0**：`v2.0.0`、`v2.1.0`、`v1.0.0`、`v1.1.0` 与 `v0.4.0` 可在应用内检查更新，经一跳桥接进入零依赖本地 Web UI 全量迁移器，自动下载校验 3.0.0 完整包并无损迁移 `config.json`、`jobs/`、`user-presets/`、`cache/accounts/`、`publish-packages/`、`dependencies/optional/` 与已下载的 Qwen 权重（原旧版目录保持不变）；`v0.3.x` / `v0.4.1` 推荐直接运行 `VWA-Online-Installer-3.0.0-x64.exe` 指向原目录执行无损覆盖升级。
- **目录完整性**：请保留程序旁的 `dependencies`、`runtime/qt6`、`models`、`assets`、`components` 和 `vendor`。
- **故障排查**：启动错误见程序目录 `launcher.log`；运行 `tools/Start-Diagnostics.bat` 查看启动诊断，运行 `tools/Check-Dependencies.bat` 核对运行库；反馈问题时可先在应用内点击 **设置与支持 → 本机诊断 → 复制摘要**。

项目代码使用 [MIT 许可](LICENSE)，第三方许可见[授权说明](THIRD_PARTY_NOTICES.zh-CN.md)与[v3.0.0 运行库说明](docs/ThirdPartyRuntime.md)。[项目仓库](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant)。
