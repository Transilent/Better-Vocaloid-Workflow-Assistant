# 结构说明

[English](Architecture.en.md) | [简体中文](Architecture.zh-CN.md) | [README](../README.zh-CN.md)

## 处理流程

BV / 链接先读取来源并下载；本地音乐先保存副本。两者统一解码为音频，再分离和提取歌词 MIDI。人工调音与剪辑后，助手为成品准备封面、署名和上传页面。

本地音乐不查询 B站来源或封面，来源署名由人填写。输入副本保存哈希，可用于继续任务。

模型双轨先用 MDX 伴奏模型估计合并人声与伴奏，再用 karaoke 模型估计人声中的和声，残差作为主唱。它按音乐角色估计，无法保证按歌姬身份分离。外部分轨可跳过模型分离。Vocal2Midi 独立处理各声部，助手保持时间位置合并 MIDI。

可选 BS-RoFormer 使用相同的第一级分离，其原权重从总人声估计主唱，再用总人声减主唱得到和声。不会因为主唱音量小而交换声部。中日文的分离参数相同：44.1 kHz 立体声、20 秒切片、75% 重叠、批量 1、不启用 TTA、不独立归一化声部。

侧栏依次为准备工作流、调音工作流、合成视频、发布，之后为模型与组件、任务与结果、设置与更新。准备步骤展开后使用完整宽度，处理设备在顶部弹窗调整。独立调音选择已有任务或导入SVP，固定输入快照，在内存中组合互斥参数来源及可选风格／音高增量，统一加密并提交一份候选。手动校准拟合及兼容旧任务导出也使用同一引擎，限制与恢复方式见[调音工作流](TuningWorkflow.zh-CN.md)。发布仍为四个可滚动步骤，底部导航与最终主按钮位于滚动区外。封面等比例缩放，缓存管理位于设置页。旧流程记录仍可读取，处理运行在独立子进程，只有工程转换加载LibreSVIP依赖。

日语保留原 Romaji ASR 权重。集成层保留切片边界的受支持音素、检查 HubertFA 输入，并按片段隔离可恢复的数据错误；对齐报告区分 ASR 为空与对齐失败。日语短音节修复只跨越不超过 150 毫秒的间隙；不发声音节也消耗对应假名，避免后续歌词错位。中文继续使用 Qwen。

VWA外观使用深浅语义配色、共享Qt样式及应用内缓存背景。单选卡适配原combo的值与model变化，外观偏好合并写入工作区的独立命名空间。窗口保留系统边框，未启用原生Windows背景接口。具体见[工作空间说明](Frontend.zh-CN.md)。

## 代码

| 文件 | 职责 |
| --- | --- |
| `launch.py`、`app.py` | 启动、界面、后台任务 |
| `desktop_theme.py`、`workspace_ui.py`、`publishing_layout.py` | 主题、侧栏工作区与发布表单 |
| `workflow_widgets.py` | 步骤导航及等比例封面预览 |
| `components_ui.py`、`optional_components.py`、`components/roformer.json` | 下载界面、冻结清单、续传安装与验证 |
| `roformer.py` | 可选 Pymss 推理，保留主唱、和声完整时间线 |
| `common.py`、`check_dependencies.py` | 路径、配置、进程取消、依赖校验 |
| `pipeline.py` | 输入副本、来源、下载、阶段、续跑 |
| `workflow.py`、`processing_workflow.py`、`processing_ui.py` | 旧预设迁移、步骤卡片与参数复用 |
| `tuning_ui.py`、`tuning_workflow.py`、`tuning_engine.py` | 独立调音页面、固定输入会话、互斥来源组合、统一加密和候选提交保护 |
| `progress_state.py`、`error_recovery.py` | 片段进度、已用时间、错误分类与 CPU 恢复 |
| `task_store.py`、`results_ui.py` | 任务资料、筛选界面、中断判断与受限删除 |
| `storage.py`、`storage_ui.py` | 可清理目录、异步扫描与确认清理 |
| `subtitles.py`、`subtitles_ui.py` | 歌词时间、MIDI 变速换算、编辑和 SRT / WebVTT |
| `mdx.py`、`separate_audio.py` | ONNX 分离与命令行入口 |
| `v2m_runtime.py`、`midi_bridge.py` | 集成转 MIDI 推理 |
| `midi_checks.py`、`midi_merge.py` | 核对提示、MIDI 校验与合轨 |
| `cover_art.py`、`publication.py` | 原始封面、补边、发布包 |
| `publishing_ui.py`、`publish_browser.py` | 登录及上传填写 |
| `publish-selectors.json` | 平台页面选择器 |
| `tools/build_portable.py` | 按清单构建 ZIP 和分卷 |
| `tools/Install-Runtime.ps1` | 固定的旧版源码启动路径；读取 BVWA 根目录兼容元数据与 Release ZIP |

## 配置与数据

`config.json` 保存应用设置，干净发行包使用相对程序目录的路径。`dependencies/manifest.json` 保存固定依赖和 SHA-256；`source-files.json` 记录仓库，`portable-files.json` 选择入包运行文件，并把简明中英文指南映射为包内 README。ZIP 内另有完整文件清单 `release-inventory.json`。

当前 VWA 包由 `portable_downloads.full` 与 `portable_downloads.jpn` 标识，分卷使用各自对应的便携元数据。顶层 `release-assets.json` 的 `archive` 与 `parts` 字段保留为供固定 `Install-Runtime.ps1` 路径使用的 BVWA 根目录兼容全量包。测试、截图、构建资料留在源码仓库；运行清单移除依赖测试目录与未使用的上游桌面界面。诊断批处理放入 `tools`，新包只保留一个规范启动入口 `VWA.exe`。

`jobs/` 保存任务素材与结果，`cache/` 保存临时文件和平台登录，`publish-packages/` 保存成品文件的路径与哈希、封面和发布文本，不复制完整成品视频。这些运行目录含私人数据，不进入 Git 和干净发行包。分发使用过的程序目录前，需移除运行数据。

续跑检查阶段结果与相关设置，已完成声部可复用。输入副本改变或结果损坏时，应重做相应阶段。

可选文件存于 `dependencies/optional/bs-roformer/current`，不进入 Git 或基础包。CPU/CUDA 运行库仅由推理子进程通过独立导入路径使用，基础 ONNX 环境不变。下载核对大小与 SHA-256；暂存环境必须通过原模型加载与可用设备运算验证才启用。安装成功后清理下载归档，取消时保留下载以便重试，原有可用环境继续保留。只保留一套已启用的可选运行库。PyTorch 不安装 C++ 头文件及 CMake 开发文件，保留 Python 推理、DLL 和许可元数据。

## 自动化范围

模型运行于本地子进程，网站通过 Playwright 页面控件操作，并使用专用浏览器会话。上传后保留页面，最终发布由人完成。网站改版可能使选择器失效。调音由人完成；视频合成是独立可选页面，发布已有成品不需要经过合成。

## 程序更新

`download_sources.py` 管理公共下载源；`updater.py` 检查 VWA 正式版本、基础运行库兼容性和程序文件清单，下载到暂存目录。`updates_ui.py` 提供设置与进度。界面与 `VWA.exe` 退出后，`tools/update_apply.py` 校验并替换程序文件，失败时恢复原文件。配置、任务、浏览器登录和模型环境均不在更新文件白名单内。用户测试确认后，`tools/build_app_update.py` 可生成小型程序更新包。

任务默认保存到 `jobs/`，也可使用自选父目录。所选目录及以前使用的任务父目录记录在私有的 `cache/workspace.json`，用于跨目录查找任务。请求记录输出父目录；继续任务使用实际已有任务路径，重新处理使用当前所选父目录新建任务。

歌词参考与字幕匹配独立管理来源及草稿。新 GUI 任务固定使用 v2 后端，旧 request 保持原后端。网易云参考默认仅填入主唱，字幕导入不会改写参考歌词。字幕界面只接入网易云时间轴或带歌词的 MIDI；不含时间的文本在转换层即拒绝。来源选择记录到任务与处理预设，歌词正文不进入预设。

## 运行与存储边界

各阶段记录输入签名与输出哈希，音频另检查采样率、声道、采样数和有限数值。MIDI 缓存只绑定识别输入、实际模型和提取参数；主题及最终工程格式不参与。旧任务通过校验后补充阶段记录。

CPU 和日语任务在单个顺序工作进程中复用原生推理会话。中文 GPU 任务仍按声部释放模型，避免下一轮 ASR 与已有模型争用显存。中文 CPU 的 ASR 常驻还要求足够的可用主存。DirectML 显卡选择和可选 CPU 线程限制在引擎之间共用。

工程和音频逐个文件提交、校验与续跑，损坏一种格式只重做对应文件。多个音频编码器共享一次声部解码。分离、图层导入和静音检查采用分块缓冲。分段下载写入一个临时文件和少量续传记录，最终哈希通过后才启用文件。

任务列表在后台扫描，按资料文件的大小和修改时间缓存。磁盘扫描不跟随链接。清理拒绝活动任务、上传、安装以及包含已登记自定义任务的缓存目录。

启动检查基础运行库，任务按所需功能检查依赖。日语 Romaji ASR 和纯音符提取无需 Qwen 权重。模型页面提供中文原权重的下载与修复入口。原生 ASR DLL 从已有目录加载。可用 `tools/build_portable.py --plan-profile` 查看语言、发布功能的依赖占用计划；该操作不会生成或发布压缩包。

[更新协议](UpdateProtocol.zh-CN.md)按来源运行库哈希选择当前路径。固定第一跳恰好包含三个文件，并在替换前暂存匹配的运行库／程序增量。已发布 0.4.0／0.4.1 客户端继续使用固定的 BVWA 名称旧版入口；其他旧运行库可使用规范的 `VWA-Updater.exe`，发行提供时也可使用逐字节一致的 BVWA 引导包别名。旧客户端写入的私有状态仍可读取，应用会同时取得旧版与规范启动器锁。新包只含 `VWA.exe`；符合条件且已有旧启动器的升级安装可保留隐藏的 `BVWA.exe` 快捷别名。

## 任务状态与文件依赖

`task_contract.py` 汇总各模块的回执，保留原有推理和导出缓存校验。`status.json` 记录实际执行计划；人工完成的工程、Mixdown、视频和发布准备另行登记。缓存清理也读取文件引用，保护仍在使用的输入。职责、迁移和恢复规则见[任务状态与文件记录](TaskContracts.zh-CN.md)。
