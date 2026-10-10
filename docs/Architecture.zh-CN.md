# 结构说明

[English](Architecture.en.md) | [简体中文](Architecture.zh-CN.md) | [README](../README.zh-CN.md)

## 处理流程

BV / 链接先读取来源并下载；本地音乐先保存副本。两者统一解码为音频，再分离和提取歌词 MIDI。人工调音与剪辑后，助手为成品准备封面、署名和上传页面。

本地音乐不查询 B站来源或封面，来源署名由人填写。输入副本保存哈希，可用于继续任务。

模型双轨先用 MDX 伴奏模型估计合并人声与伴奏，再用 karaoke 模型估计人声中的和声，残差作为主唱。它按音乐角色估计，无法保证按歌姬身份分离。外部分轨可跳过模型分离。Vocal2Midi 独立处理各声部，助手保持时间位置合并 MIDI。

可选 BS-RoFormer 使用相同的第一级分离，其原权重从总人声估计主唱，再用总人声减主唱得到和声。不会因为主唱音量小而交换声部。中日文的分离参数相同：44.1 kHz 立体声、20 秒切片、75% 重叠、批量 1、不启用 TTA、不独立归一化声部。

Qt 界面使用八页侧栏，工作流节点集中处理参数和设备，验证连线后保存任务的执行终点。预设、当前参数和输出历史保存在私人 `cache/workspace.json`。片段进度写入独立 JSON，阶段状态记录完成与已用时间。任务列表通过原有文件锁判断异常退出；缓存清理仅使用明确定义的可清理目录，拒绝链接及运行中的任务。发布页保留一个主要操作，依据发布包是否对应当前表单切换“生成资料”和“上传”。

日语保留原 Romaji ASR 权重。集成层保留切片边界的受支持音素、检查 HubertFA 输入，并按片段隔离可恢复的数据错误；对齐报告区分 ASR 为空与对齐失败。日语短音节修复只跨越不超过 150 毫秒的间隙；不发声音节也消耗对应假名，避免后续歌词错位。中文继续使用 Qwen。

## 代码

| 文件 | 职责 |
| --- | --- |
| `launch.py`、`app.py` | 启动、界面、后台任务 |
| `desktop_theme.py`、`workspace_ui.py`、`publishing_layout.py` | 主题、侧栏工作区与发布表单 |
| `components_ui.py`、`optional_components.py`、`components/roformer.json` | 下载界面、冻结清单、续传安装与验证 |
| `roformer.py` | 可选 Pymss 推理，保留主唱、和声完整时间线 |
| `common.py`、`check_dependencies.py` | 路径、配置、进程取消、依赖校验 |
| `pipeline.py` | 输入副本、来源、下载、阶段、续跑 |
| `workflow.py`、`workflow_ui.py` | 连线验证、私人预设、原生节点画布 |
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
| `tools/Install-Runtime.ps1` | 源码启动前安装、Release 下载、依赖校验 |

## 配置与数据

`config.json` 保存应用设置，干净发行包使用相对程序目录的路径。`dependencies/manifest.json` 保存固定依赖和 SHA-256；`source-files.json` 记录仓库，`portable-files.json` 选择入包运行文件，并把简明中英文指南映射为包内 README。ZIP 内另有完整文件清单 `release-inventory.json`。

Release 只提供三份分卷、合并脚本及最小归档/分卷校验清单。测试、截图、构建资料留在源码仓库；运行清单移除依赖测试目录与未使用的上游桌面界面。诊断批处理放入 `tools`，根目录只保留一个程序启动入口。

`jobs/` 保存任务素材与结果，`cache/` 保存临时文件和平台登录，`publish-packages/` 保存成品副本与发布资料。这些运行目录含私人数据，不进入 Git 和干净发行包。分发使用过的程序目录前，需移除运行数据。

续跑检查阶段结果与相关设置，已完成声部可复用。输入副本改变或结果损坏时，应重做相应阶段。

可选文件存于 `dependencies/optional/bs-roformer/current`，不进入 Git 或基础包。CPU/CUDA 运行库仅由推理子进程通过独立导入路径使用，基础 ONNX 环境不变。下载核对大小与 SHA-256；暂存环境必须通过原模型加载与可用设备运算验证才启用。安装成功后清理下载归档，取消时保留下载以便重试，原有可用环境继续保留。只保留一套已启用的可选运行库。PyTorch 不安装 C++ 头文件及 CMake 开发文件，保留 Python 推理、DLL 和许可元数据。

## 自动化范围

模型运行于本地子进程，网站通过 Playwright 页面控件操作，并使用专用浏览器会话。上传后保留页面，最终发布由人完成。网站改版可能使选择器失效。调音及成品音视频合成仍由人完成。

## 程序更新

`download_sources.py` 管理公共下载源；`updater.py` 检查正式发布版本、基础运行库兼容性和程序文件清单，下载到暂存目录。`updates_ui.py` 提供设置与进度。界面与 EXE 启动器退出后，`tools/update_apply.py` 校验并替换程序文件，失败时恢复原文件。配置、任务、浏览器登录和模型环境均不在更新文件白名单内。用户测试确认后，`tools/build_app_update.py` 可生成小型程序更新包。

任务默认保存到 `jobs/`，也可使用自选父目录。所选目录及以前使用的任务父目录记录在私有的 `cache/workspace.json`，用于跨目录查找任务。请求记录输出父目录；继续任务使用实际已有任务路径，重新处理使用当前所选父目录新建任务。
