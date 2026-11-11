# 结构说明

[English](Architecture.en.md) | [简体中文](Architecture.zh-CN.md) | [README](../README.zh-CN.md)

## 处理流程

BV / 链接先读取来源并下载；本地音乐先保存副本。两者统一解码为音频，再分离和提取歌词 MIDI。人工调音与剪辑后，助手为成品准备封面、署名和上传页面。

本地音乐不查询 B站来源或封面，来源署名由人填写。输入副本保存哈希，可用于继续任务。

模型双轨先用 MDX 伴奏模型估计合并人声与伴奏，再用 karaoke 模型估计人声中的和声，残差作为主唱。它按音乐角色估计，无法保证按歌姬身份分离。外部分轨可跳过模型分离。Vocal2Midi 独立处理各声部，助手保持时间位置合并 MIDI。

可选 BS-RoFormer 使用相同的第一级分离，其原权重从总人声估计主唱，再用总人声减主唱得到和声。不会因为主唱音量小而交换声部。中日文的分离参数相同：44.1 kHz 立体声、20 秒切片、75% 重叠、批量 1、不启用 TTA、不独立归一化声部。

Quick 侧栏包括准备、调音、简易混音、合成视频、发布，以及模型与组件、任务与结果、设置与支持。调音、混音是连续工作区；切页不销毁控制器或正在进行的试听。准备和发布以步骤组织。旧任务和工作流记录继续由共享服务读取，处理任务仍在隔离子进程中运行。

日语保留原 Romaji ASR 权重。集成层保留切片边界的受支持音素、检查 HubertFA 输入，并按片段隔离可恢复的数据错误；对齐报告区分 ASR 为空与对齐失败。日语短音节修复只跨越不超过 150 毫秒的间隙；不发声音节也消耗对应假名，避免后续歌词错位。中文继续使用 Qwen。

Qt Quick 使用共享主题、语义配色和缓存背景。窗口保留系统边框，常用布局为按屏幕适配的默认档和最大化。见[工作空间说明](Frontend.zh-CN.md)。

## 代码

| 文件 | 职责 |
| --- | --- |
| `quick_mixdown.py`、`mixdown_workspace_state.py`、`mixdown_request.py`、`mixdown_preview_controller.py`、`mixdown_export.py`、`mixdown_runtime.py` | 简易混音的界面、纯参数投影、试听控制、冻结导出和运行库职责分开；详见[混音架构](MixdownArchitecture.zh-CN.md)。 |
| [preset_documents.py](../src/preset_documents.py)、`preset_builtin_documents.py`、`preset_library.py`、`quick_support.py`、`preset_cli.py` | 统一格式、本机库附加导入和 CLI；`mixdown_preset_documents.py` / `quick_mixdown.py` 接入混音参数捕获、范围预览、应用和撤销。 |
| `launch.py`、`quick_app.py` | 启动、界面、后台任务 |
| `appearance_state.py`、`quick_bridge.py`、`quick_ui/PublicationPage.qml` | 主题、使用稳定页面 ID 并兼容旧跳转的侧栏工作区、发布表单 |
| `component_operations.py`、`optional_components.py`、`components/roformer.json` | 下载界面、冻结清单、续传安装与验证 |
| [component_health.py](../src/component_health.py) | 将快速元数据检查与显式完整校验分开；约束路径、记录预算、取消和校验期间的文件身份 |
| [style_catalog.py](../src/style_catalog.py)、[expression_controls.py](../src/expression_controls.py) | GUI、MCP、草稿和执行层共用风格元数据及表现参数规格；旧 ID 配方与各用途步长保留 |
| [multi_file_commit.py](../src/multi_file_commit.py)、`vocal_partition.py` | 相关输出统一备份、发布、验证和失败恢复；回执完成后才清理恢复副本 |
| `roformer.py` | 可选 Pymss 推理，保留主唱、和声完整时间线 |
| `common.py`、`check_dependencies.py` | 路径、配置、进程取消、共享单字节文件锁、依赖校验 |
| `pipeline.py` | 输入副本、来源、下载、阶段、续跑 |
| `workflow.py`、`preparation_commands.py`、`quick_ui/PreparationPage.qml` | 旧预设迁移、步骤卡片与参数复用 |
| `quick_tuning.py`、`tuning_workflow.py`、`tuning_engine.py` | 独立调音页面、固定输入会话、互斥来源组合、统一加密和候选提交保护 |
| `progress_state.py`、`error_recovery.py` | 片段进度、已用时间、错误分类与 CPU 恢复 |
| `task_store.py`、`quick_results.py` | 任务资料、筛选界面、中断判断与受限删除 |
| `storage.py`、`quick_support.py` | 可清理目录、异步扫描与确认清理 |
| `subtitles.py`、`quick_subtitles.py` | 歌词时间、MIDI 变速换算、编辑和 SRT / WebVTT |
| `mdx.py`、`separate_audio.py` | ONNX 分离与命令行入口 |
| `v2m_runtime.py`、`midi_bridge.py` | 集成转 MIDI 推理 |
| `midi_checks.py`、`midi_merge.py` | 核对提示、MIDI 校验与合轨 |
| `cover_art.py`、`publication.py` | 原始封面、补边、发布包 |
| `quick_publication.py`、`publish_browser.py` | 登录及上传填写 |
| `publish-selectors.json` | 平台页面选择器 |
| `tools/build_portable.py` | 按清单构建 ZIP 和分卷 |

## 配置与数据

`config.json` 保存应用设置，干净发行包使用相对程序目录的路径。`dependencies/manifest.json` 保存固定依赖和 SHA-256；`source-files.json` 记录仓库，`portable-files.json` 选择入包运行文件，并把简明中英文指南映射为包内 README。ZIP 内另有完整文件清单 `release-inventory.json`。

当前开发入口为 `launch.py` → `quick_app.py`。完整 Quick 便携包另含独立的 `runtime/qt6`，处理 Python 中的旧 Qt 5 文件不进入新包。诊断脚本进入同一应用。已发布的 1.1.0 和其首跳协议属于历史发行；它们的制品与冻结客户端保持不变。

`jobs/` 保存任务素材与结果，`cache/` 保存临时文件和平台登录，`publish-packages/` 保存成品文件的路径与哈希、封面和发布文本，不复制完整成品视频。这些运行目录含私人数据，不进入 Git 和干净发行包。分发使用过的程序目录前，需移除运行数据。

续跑检查阶段结果与相关设置，已完成声部可复用。输入副本改变或结果损坏时，应重做相应阶段。

可选文件存于 `dependencies/optional/bs-roformer/current`，不进入 Git 或基础包。CPU/CUDA 运行库仅由推理子进程通过独立导入路径使用，基础 ONNX 环境不变。下载核对大小与 SHA-256；暂存环境必须通过原模型加载与可用设备运算验证才启用。安装成功后清理下载归档，取消时保留下载以便重试，原有可用环境继续保留。只保留一套已启用的可选运行库。PyTorch 不安装 C++ 头文件及 CMake 开发文件，保留 Python 推理、DLL 和许可元数据。

## 自动化范围

模型运行于本地子进程。B站填写通过 Playwright 和专用浏览器会话，最终发布由人完成；网站改版可能使选择器失效。小红书走本地资料交接和显式 AI 声明，不创建自动化会话。调音由人完成；视频合成是独立可选页面，发布已有成品不需要经过合成。

## 程序更新

`quick_support.py` 提供更新发现、说明、暂缓和恢复。Quick 程序更新要求已安装 Qt 版本、处理运行库摘要和目标界面身份同时匹配，并验证程序白名单。跨界面或 Qt 版本变化使用适用的完整便携包。更新事务复用 `tools/update_apply.py`；旧首跳身份保留供历史记录读取。配置、任务、登录及模型不属于程序更新内容。

任务默认保存到 `jobs/`，也可使用自选父目录。所选目录及以前使用的任务父目录记录在私有的 `cache/workspace.json`，用于跨目录查找任务。请求记录输出父目录；继续任务使用实际已有任务路径，重新处理使用当前所选父目录新建任务。

歌词参考与字幕草稿独立。字幕可使用网易云时间轴、带歌词事件的 MIDI 或定时 LRC；三种来源保留各自草稿，字幕导入不会改写准备页歌词。预设只保存可复用选项。

## 运行与存储边界

各阶段记录输入签名与输出哈希，音频另检查采样率、声道、采样数和有限数值。MIDI 缓存只绑定识别输入、实际模型和提取参数；主题及最终工程格式不参与。旧任务通过校验后补充阶段记录。

CPU 和日语任务在单个顺序工作进程中复用原生推理会话。中文 GPU 任务仍按声部释放模型，避免下一轮 ASR 与已有模型争用显存。中文 CPU 的 ASR 常驻还要求足够的可用主存。DirectML 显卡选择和可选 CPU 线程限制在引擎之间共用。

工程和音频逐个文件提交、校验与续跑，损坏一种格式只重做对应文件。多个音频编码器共享一次声部解码。分离、图层导入和静音检查采用分块缓冲。分段下载写入一个临时文件和少量续传记录，最终哈希通过后才启用文件。

任务列表在后台扫描，按资料文件的大小和修改时间缓存。磁盘扫描不跟随链接。清理拒绝活动任务、上传、安装以及包含已登记自定义任务的缓存目录。

启动检查基础运行库，任务按所需功能检查依赖。日语 Romaji ASR 和纯音符提取无需 Qwen 权重。模型页面提供中文原权重的下载与修复入口。原生 ASR DLL 从已有目录加载。可用 `tools/build_portable.py --plan-profile` 查看语言、发布功能的依赖占用计划；该操作不会生成或发布压缩包。

[更新协议](UpdateProtocol.zh-CN.md)按来源运行库哈希选择当前路径。固定第一跳恰好包含三个文件，并在替换前暂存匹配的运行库／程序增量。已发布 0.4.0／0.4.1 客户端继续使用固定的 BVWA 名称旧版入口；其他旧运行库可使用规范的 `VWA-Updater.exe`，发行提供时也可使用逐字节一致的 BVWA 引导包别名。旧客户端写入的私有状态仍可读取，应用会同时取得旧版与规范启动器锁。新包只含 `VWA.exe`；符合条件且已有旧启动器的升级安装可保留隐藏的 `BVWA.exe` 快捷别名。

## 任务状态与文件依赖

`task_contract.py` 汇总各模块的回执，保留原有推理和导出缓存校验。`status.json` 记录实际执行计划；人工完成的工程、Mixdown、视频和发布准备另行登记。缓存清理也读取文件引用，保护仍在使用的输入。职责、迁移和恢复规则见[任务状态与文件记录](TaskContracts.zh-CN.md)。
