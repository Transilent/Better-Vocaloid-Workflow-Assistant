# 第三方说明

[English](THIRD_PARTY_NOTICES.md) | [简体中文](THIRD_PARTY_NOTICES.zh-CN.md) | [README](README.zh-CN.md)

便携包含第三方组件和模型，各自的许可证与声明仍适用。项目名称不改变其归属或授权；助手原创代码采用 MIT；第三方代码与权重的授权仍分别适用。

| 组件 | 来源与声明 |
| --- | --- |
| Vocal2Midi | [上游项目](https://github.com/Xiantaidu/Vocal2Midi)，Apache-2.0；保留 `dependencies/vocal2midi/LICENSE` 与 `ACKNOWLEDGEMENTS.md` |
| MDX 分离模型 | [UVR 模型发布](https://github.com/TRvlvr/model_repo/releases/tag/all_public_uvr_models)、[模型参数](https://github.com/TRvlvr/application_data)；模型授权与助手代码不同 |
| 可选 BS-RoFormer 模型 | [becruily/bs-roformer-karaoke](https://huggingface.co/becruily/bs-roformer-karaoke) 的原权重与配置；版本、哈希见 `components/roformer.json`；模型授权独立适用 |
| 可选 Pymss、Pymss Core、PyTorch 及支持库 | 从冻结的上游 wheel 下载，包元数据及原声明保留于 `dependencies/optional/bs-roformer/current/packages` |
| Python | 3.12.10，保留运行库的 `LICENSE.txt` |
| Python 库 | 保留原 `LICENSE`、`COPYING`、包内许可及元数据 |
| yt-dlp | [上游](https://github.com/yt-dlp/yt-dlp)，`vendor/` 内保留代码与许可 |
| Playwright / Node.js | [上游](https://github.com/microsoft/playwright-python)，`vendor/` 内保留原声明 |
| FFmpeg | 7.1.3-Jellyfin，启用 GPL/version3；[源码](https://github.com/jellyfin/jellyfin-ffmpeg)、[许可说明](https://ffmpeg.org/legal.html) |
| Google Chrome | 浏览器运行库；[使用条款](https://www.google.com/chrome/terms/)，开源组件见 `chrome://credits` |

Vocal2Midi 的上游资料保留 GAME、HubertFA、Qwen3-ASR、RMVPE、RomajiASR、llama.cpp、ONNX Runtime 的致谢。固定环境经过验证，不代表所有第三方文件具有相同许可证。

侧栏、卡片和操作分组参考 [Pymss Studio](https://github.com/pymss-project/pymss-studio)。本助手的 Qt 布局与 SVG 控件为原创实现，未打包 Studio 的前端代码或素材。

基础包不含可选 BS-RoFormer 运行库，也不含浏览器账号、会话、私人音视频、封面、MIDI 或发布草稿。二进制包中的第三方组件仍需遵守各自的再分发条款。

LibreSVIP 2.9.2 核心及依赖保存在 `dependencies/export`，保留各包的许可与元数据；无需其桌面 GUI。Vocal2Midi 2.0 的应用层与推理代码保存在 `dependencies/vocal2midi2`，复用已有模型与 Python，保留 Apache-2.0、NOTICE 与致谢。助手对日语 HFA 边界修复增加 150 ms 间隔限制，避免把较长停顿合并进音节。网易云歌词入口使用独立实现的公开接口客户端；163MusicLyrics 仅作为行为参考，未复制或分发其程序。卡片布局参考 MaaEnd 截图，Qt 实现由本项目编写。

集成适配另按语言检查原模型路径，并共用 DirectML 显卡与 CPU 线程设置。原权重不变。助手运行库精简未使用的上游 GUI 模块与 Qt 功能，保留所需包的原许可证资料。
