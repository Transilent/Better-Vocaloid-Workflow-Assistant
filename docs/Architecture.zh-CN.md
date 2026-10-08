# 结构说明

[English](Architecture.en.md) | [简体中文](Architecture.zh-CN.md) | [README](../README.zh-CN.md)

## 处理流程

BV / 链接先读取来源并下载；本地音乐先保存副本。两者统一解码为音频，再分离和提取歌词 MIDI。人工调音与剪辑后，助手为成品准备封面、署名和上传页面。

本地音乐不查询 B站来源或封面，来源署名由人填写。输入副本保存哈希，可用于继续任务。

模型双轨先用 MDX 伴奏模型估计合并人声与伴奏，再用 karaoke 模型估计人声中的和声，残差作为主唱。它按音乐角色估计，无法保证按歌姬身份分离。外部分轨可跳过模型分离。Vocal2Midi 独立处理各声部，助手保持时间位置合并 MIDI。

## 代码

| 文件 | 职责 |
| --- | --- |
| `launch.py`、`app.py` | 启动、界面、后台任务 |
| `common.py`、`check_dependencies.py` | 路径、配置、进程取消、依赖校验 |
| `pipeline.py` | 输入副本、来源、下载、阶段、续跑 |
| `mdx.py`、`separate_audio.py` | ONNX 分离与命令行入口 |
| `v2m_runtime.py`、`midi_bridge.py` | 集成转 MIDI 推理 |
| `midi_checks.py`、`midi_merge.py` | 核对提示、MIDI 校验与合轨 |
| `cover_art.py`、`publication.py` | 原始封面、补边、发布包 |
| `publishing_ui.py`、`publish_browser.py` | 登录及上传填写 |
| `publish-selectors.json` | 平台页面选择器 |
| `tools/build_portable.py` | 按清单构建 ZIP 和分卷 |

## 配置与数据

`config.json` 保存应用设置，干净发行包使用相对程序目录的路径。`dependencies/manifest.json` 保存固定依赖和 SHA-256；`source-files.json` 选择入包源码。ZIP 内另有完整文件清单 `release-inventory.json`。

`jobs/` 保存任务素材与结果，`cache/` 保存临时文件和平台登录，`publish-packages/` 保存成品副本与发布资料。这些运行目录含私人数据，不进入 Git 和干净发行包。分发使用过的程序目录前，需移除运行数据。

续跑检查阶段结果与相关设置，已完成声部可复用。输入副本改变或结果损坏时，应重做相应阶段。

## 自动化范围

模型运行于本地子进程，网站通过 Playwright 页面控件操作，并使用专用浏览器会话。上传后保留页面，最终发布由人完成。网站改版可能使选择器失效。调音及成品音视频合成仍由人完成。
