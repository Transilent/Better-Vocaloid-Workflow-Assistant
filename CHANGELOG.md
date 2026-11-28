# Changelog

## Unreleased

## 3.0.0 — 2026-11-28

### 主要新增功能
- **全新修音与合唱工作台（Chorus Workbench）**：支持多轨人声干声与 MIDI 对照修音、逐句复核与精准手绘音高、音节力度调节、Signalsmith Stretch 原生高保真连续变调（8 kHz tonality / 共振峰保留）、CAS 缓存与分轨干声交付。
- **独立工程格式安全互转页（Project Converter）**：支持 12 种歌声合成工程（SVP/SV2、USTX、VPR、VSQX、ACEP、UFData、MIDI 等）互转、轨拆分、回读体检与时间轴 LRC/SRT 歌词导出，绝不覆盖源文件。
- **简易混音升级至 16 轨原生实时音频引擎**：新增 WASAPI 音频设备枚举与热插拔自动重连、原生压限/输出 DLL、60fps FBO 波形与草稿崩溃恢复。
- **BS-RoFormer 双运行库共存与实验性 AMD WebGPU ONNX 后端**：
  - 原版 PyTorch BS-RoFormer 支持 GPU (CUDA) 与 CPU 独立共存与权重复用；
  - 新增默认不启用的独立实验组件 **BS-RoFormer ONNX（10s FP16 / 20s FP16）**（致谢 `@AU-ki`），隔离运行 `onnxruntime-webgpu 1.27.0`，针对 AMD 显卡按显存智能推荐档位，权重已镜像托管至主仓库。
- **组件页管理状态全面清晰化**：新增左侧“使用中”与详情页“当前已选用”标识、分档就绪标签、安装/校验按钮显式标明目标档位、实时进度条，并修复浅色主题胶囊对比度与窄屏折行问题。
- **全量 UX 与基础设施升级**：4 场景新手引导与 11 步非阻塞导览、全局快捷命令（`Ctrl+K`）与快捷键速查（`Ctrl+/`）、窗口级拖拽路由、任务栏进度、Toast 提示栈、Happy Eyeballs 镜像竞速、批量准备队列、SQLite 任务索引与 Windows MiniDump 捕获。

### 关键修复与兼容性
- 解除 Synthesizer V Studio 2 工程版本硬上限，支持 `version: 203+` 独立音符组读写，同时保留共享组与裁切组的只读保护。
- 为 Vocal2Midi v1/v2 的 RMVPE 推理新增 DirectML 显卡重置（`0x887A0005` 等）与中文系统 GBK 解码异常自动回退 CPU，并接入 `soxr` 快速重采样与 Qwen 显存复用。
- 准备页显卡选择器显示真实 GPU 名称与显存容量；修复 13 个 QML 文件窗口退出时的 `quickTranslation` 空指针隐患。
- **全版本二跳全量更新与自动数据迁移器**：支持旧版用户通过一跳桥接进入零依赖本地 Web UI 全量下载迁移器，校验下载 3.0.0 Full/JPN 分卷包后自动迁移 `jobs/`、`config.json`、`user-presets/`、`cache/accounts/` 与已下载的可选模型并启动新版本；同步新增 **零依赖单文件在线引导安装器（`VWA-Online-Installer.exe`，支持中/英/日三语、深浅主题、Full/JPN 版本卡片自选、国内镜像竞速与多线程分片续传，覆盖升级无损保留用户数据）**，并保留绿色便携版与 Inno Setup 6 离线安装器。
- **安全与运行库升级**：便携版 `runtime/python` 升级 `certifi 2026.10.18`、`urllib3 2.8.0`、`msgpack 1.2.3`、`pip 26.2`、`setuptools 84.1.0`，MCP 锁文件升级 `opentelemetry-api 1.45.2`，清零全部 10 条 Dependabot 安全告警；新增 CodeQL SAST、AST 安全反序列化守卫与 CI 卫生检查。

## 2.1.0 — 2026-11-20

- 在调音卷帘内改词、移动或缩放音符，并在参数轨用曲线笔、直线、节点和橡皮编辑音高。单轨检查器显示所选音符可写的原生参数。
- 新增 VSQX3／4、ACEP 和 UFData 调音支持。VPR、VSQX、USTX 与可写 ACEP 可逐轨应用参考人声，并另存为原格式。参数按工程能力提供，同格式匹配工程可沿用原曲线。
- 简易混音新增去齿音、歌声与伴奏波形对齐、A/B 响度匹配和独立混响返回。
- 准备工作流接入英文识别与 USTX 输出，网易云歌词入口恢复按歌名搜索。修正准备页格式选择保存问题 ([#3](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/3))。
- 模型与组件按实际下载地址提供加速源，下载、长任务和错误增加进度及本机诊断。新安装默认使用深色主题。
- 在应用内从正式 1.0.0、1.1.0 和 2.0.0 升级。普通版与 JPN 版均保留本地工程、设置和已安装模型。

## 2.0.0 — 2026-11-13

- Move the Windows desktop to a Qt Quick workbench with continuous preparation, tuning and mixing workflows. Add guided onboarding, startup update notices, and Settings and Support with QQ group 490619616.
- Add a piano-roll tuning workspace with track and lyric navigation, a draggable timeline overview, editable colored style regions, project-bound presets, and first-pass SV2, USTX, and VPR project workflows.
- Rebuild continuous mixing audition around a shared real-time audio session, including A/B playback, live meters, spectrum, gain reduction and reverb. Mix and export WAV files independently of a task.
- Share a versioned local preset library across preparation, mixing, tuning and style recipes. Imported presets are appended by default. Xiaohongshu export produces a local package for manual posting.

### Changes carried forward

- Connect portable mixing presets to capture, scoped before/after application and guarded one-step undo. Preserve current width and track templates by default, share the existing processing path, and append imported presets across all four kinds with an explicit independent-copy option.

- Add a versioned preset format and local library for preparation, mixing, tuning and declarative style recipes, with previewed imports, immutable exports, copies, local aliases/favorites, revision conflicts and explicit recovery. Settings exposes the manager and a shared CLI; applying library entries to processing controls remains a later stage.
- Include shared style, mixing, source, transaction and component modules in portable/update payloads, with a regression guard against missing runtime modules.
- Replace Xiaohongshu browser automation with verified local publication files, copy actions and manual posting. Require an explicit AI-content disclosure for new Xiaohongshu packages; Bilibili keeps its existing upload assistance.
- Hold the existing task lock while reusing a download and retain unverified source declarations when reprocessing local audio. Component installation and inference use the same lock while selecting and using the active runtime.
- Reject a review response if its sections were assembled from different artifact identities or the workflow revision changed during the read. Use stable desktop page IDs while retaining legacy numeric navigation, and keep video render controls locked from launch through completion.
- Centralize style discovery and expression-control boundaries across the GUI, MCP, drafts and calibration while preserving existing recipes, legacy IDs and parameter units. Explicit audio channel modes use separate feature-cache identities; legacy standard-mode caches remain reusable.
- Reuse the paired-output transaction for vocal-partition validation and rollback, retaining recovery sidecars until validation and receipt construction succeed.
- Distinguish quick optional-component metadata checks from explicit cancellable full-file verification, with progress and file-integrity results available from the component page and command line.
- Share source validation between desktop preparation and MCP. Unsupported Bilibili subpage selectors now produce an explicit error instead of silently selecting the default page.
- Bind style results to the current project generation, preserve source and default media context when cloning workflows, and retain archived lifecycle state on repeated close. Reject oversized JSON records before replacing readable state.
- Clear generated pitch scaffolding when explicitly rebuilding original F0, while retaining authored expression in ordinary style edits. Restore saved adapter and CPU-thread choices.
- Share effective EQ limits, sample-rate handling and positive vocal-bus gain-reduction semantics across audition, export and controls. Keep preview envelopes separate from export reports.
- Preserve new cancellation requests during startup, roll back paired separation outputs together, and fall back to usable LRC when a filtered YRC contains only credits.
- Align task-file and update interlocks, handle unavailable output drives, require complete MIDI handoff, restore publication snapshots without requiring the original task, and translate dynamic recovery messages through stable templates.
- Reclaim timed-out test process trees and report partial skips explicitly. Device playback, voicebank listening and release-package validation remain separate acceptance steps.

## 1.1.0 — 2026-11-01

### Added

- Add a standalone Simple Mixing workflow for up to three rendered vocal WAVs and one accompaniment. Tracks have level, pan and mute/solo controls; vocal tracks also have timing and reverb sends. Shared vocal-bus EQ, compression, reverb and presets are listening starting points, and source files are not overwritten.
- Add continuous segmented A/B audition with playback across segment boundaries and automatic preview refresh after parameter changes when possible. If an audio output device fails, VWA retries once with the compatible internal player; if playback still cannot start, it offers the current segment in the system player. Levels are sample peaks and gain reduction is estimated.
- Add time-region tuning and style autosave, three experimental singing styles, and an independent experimental Chinese-opera technique. These produce editable project drafts and require synthesis and audition with the target voicebank.
- Add an optional MCP technical preview with 12 typed top-level tools for preparation, evidence review, decisions, execution, recovery and delivery. Setup uses a separate environment; the desktop application can be used without MCP.

### Packaging and upgrades

- The 1.1.0 full portable package is the clean-install route for all users, including new users and users on any older version.
- Provide the verified 1.0.0 update route. Direct updates from 0.4.0 and 0.4.1 are not provided in this release.
- Preserve the JPN variant when updating from 1.0.0: do not force omitted Chinese weights to download, and keep any models the user installed later.
- Manual listening on representative full songs and audio devices remains to be completed.

## 1.0.0 — 2026-10-23

- Separate preparation, tuning, optional video assembly and publication workflows, with a task library shared by workflow inputs.
- Refresh the VWA desktop interface, stage progress and functional guidance.
- Add Chinese, Japanese and English interface preferences, effective on the next launch. Lyric recognition remains Chinese/Japanese.
- Support fine-grained expression strength, optional original Chinese model installation and additional public download routes.

## 0.4.1 — 2026-10-14

### Fixed

- Honor the pitch-curve switch in all three project formats, including native intermediate projects. With pitch disabled, omit explicit curves and flat tuning presets while retaining notes, lyrics and accompaniment. Switching the option invalidates the project cache.
- Respect the maximum audio slice length when merging tiny vocal fragments, including intervening silence. Retain fragments that cannot fit, with their original timestamps. Apply the correction to both extraction backends.
- Surface the underlying subprocess exception and localized DirectML device-reset codes instead of only the exit-code summary. Normal provider-selection logs no longer classify unrelated failures as GPU errors.
- Preserve the selected download source after restarting into the upgrade window. Both upgrade stages support GitProxy, source fallback, resume and SHA-256 verification.
- Write command-line startup diagnostics to `diagnostics.log`, avoiding a log-file conflict with an already running desktop window.

### Updating

- Provide separate compatible upgrade paths for v0.3.1 and v0.4.0. Existing models, settings, tasks, sign-in profiles and optional components are retained.
- Publish update archives only. New installations can use the v0.4.0 portable package, then update through Settings/updates.

## 0.4.0 — 2026-10-13

- Support verified in-place online upgrades from v0.3.0/v0.3.1 using a compatible bootstrap and a small runtime delta; retain models, tasks and sign-in state.

- Rename the main pages to Tuning workflow and Publishing workflow, keeping them together in the sidebar.
- Guide publication through four full-page steps with larger cover previews, visible platform drafts/account controls and a fixed final action.
- Check required inputs before advancing, clear stale cover previews and show platform-specific status.
- Recheck publication prerequisites when navigating by step number; recover from missing source tasks and show clear restoration errors.
- Disable project formats, pitch-curve and accompaniment options when project export is off, preserving selections for re-enabling.
- Enlarge workflow controls and use opaque light/dark color themes with a restrained green accent and standard system window controls.
- Move per-engine devices to the tuning toolbar while preserving preset settings.

- Use Vocal2Midi 2.0 for new GUI tasks without a backend selector.
- Separate reference calibration into NetEase/local sources, with NetEase lyrics applied to the lead vocal.
- Split subtitle matching into NetEase timelines and MIDI lyric tracks; remove untimed text estimation.

- Merge workflow editing and music processing into compact expandable cards; embed subtitles and move cache management into settings.
- Add dark/light themes and reusable processing/export presets.
- Integrate the Vocal2Midi 2.0 application layer with existing Chinese/Japanese models and shared Python/FFmpeg.
- Add NetEase song/artist search, song links/IDs and lyric-file reference import.
- Export USTX, VSQX and SVP projects with pitch curves, plus WAV/FLAC/MP3/OGG stems.
- Add verified resumable parallel downloads with configurable connections and source fallback.
- License original assistant source under MIT and retain upstream notices.
- Deduplicate native ASR binaries; keep new core export libraries separate from the main runtime.
- Validate stage caches against their inputs and output hashes; repair damaged exports without regenerating intact formats.
- Retrieve publication metadata and original covers without downloading the full source video.
- Reduce intermediate audio allocations with block processing and shared decoding for multiple output codecs.
- Write resumable parallel downloads to one temporary file, avoiding duplicate range assembly.
- Reuse native inference sessions sequentially where appropriate while retaining per-voice isolation for Chinese GPU transcription.
- Add a DirectML adapter selector, optional CPU thread limits and feature-scoped dependency checks.
- Add pinned repair/download of the original Chinese ASR model; Japanese and notes-only transcription can run independently.
- Load task history asynchronously and protect custom task folders during cache cleanup.
- Fix cancellation leaving subprocesses running and concurrent JSON writes failing on Windows.
- Remove verified unused runtime files while preserving original model weights and component licenses.
- Preserve sub-10-tick note durations in native USTX exports, preventing artificial overlaps that stopped editor-project conversion.
- Surface Vocal2Midi 2.0 stage logs and live carriage-return progress updates instead of leaving the display at model loading.
- Avoid clipping compressed stem exports when float audio exceeds full scale; use shared peak attenuation, record the gain and preserve original WAV files.
- Preserve modified MIDI, project and encoded audio files in task backups before regenerating them; unchanged generated outputs do not create duplicates.


## 0.3.1 — 2026-10-11

### Fixed

- Use direct connections for Bilibili metadata, original covers and media downloads, independent of system and environment proxies.
- Apply the same direct connection policy to component downloads, program updates and runtime installation while retaining accelerated download sources.
- Use direct connections in the dedicated sign-in/upload browser.
- Clarify Bilibili connection failures and keep the existing task available for retry.

### Changed

- Check, download and apply program updates automatically at startup by default, displaying a ten-second restart countdown. Manual downloads require an explicit restart action.
- Postpone startup installation to the next launch once the user interacts with the assistant or an operation has begun; do not check periodically during use.
- Allow postponing installation or disabling automatic restart in Settings/updates.
- Save workflow preferences before applying an update and include the running version in startup diagnostics.

## 0.3.0 — 2026-10-11

- Add a draggable workflow canvas with validated execution connections, named presets and per-engine device selection.
- Let the processing page select saved workflows directly; synchronize preset choices and parameters across both pages.
- Replace two-click port wiring with proximity previews, snap-to-connect on drop and pull-apart disconnection.
- Expand task search, filtering, sorting, naming and deletion; show interrupted tasks and recorded settings.
- Show stage/subtask progress, elapsed time and download throughput; add actionable errors and CPU retry.
- Add asynchronous disk usage scans and confirmed cleanup of disposable caches and task temporary files.
- Add an offline LRC/TXT/CSV/MIDI lyric editor with SRT and WebVTT export and optional workflow subtitle output.
- Reduce the portable runtime inventory by 447 MB while retaining active model weights; default new full builds to ZIP level 6.

### Added

- A custom save directory for downloaded videos, audio and MIDI, with remembered task locations.
- Chinese MIDI lyric output in characters or pinyin, saved per task.
- Official and accelerated download routes with resume and SHA-256 verification.
- A Windows EXE launcher with an original application icon and no console window.
- Startup update checks, optional automatic downloads, and verified restart-to-install updates.
- Update rollback that preserves configuration, tasks, sign-in profiles and installed models.

### Changed

- Increased the default workspace to 1600 × 1000, with screen-size adaptation.
- Larger typography, translucent cards, gradient controls and animated hover highlights.
- Grouped diagnostic scripts in `tools/` and revised interface/help text.
- Revised English and Chinese documentation and release instructions.
- Set the Chinese application name to 术力口工作流助手.

## 0.2.0 — 2026-10-09

- Complete portable package with the redesigned workspace and optional BS-RoFormer installation.
- Retained Japanese ASR weights and alignment improvements.
- Simplified release attachments and portable runtime contents.

## 0.1.2 — 2026-10-08

- Japanese alignment fixes and MIDI import guidance.

## 0.1.1 — 2026-10-08

- Runtime installation for source checkouts.

## 0.1.0 — 2026-10-08

- Initial portable workflow release.
