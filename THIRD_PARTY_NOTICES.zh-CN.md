# 第三方说明

[English](THIRD_PARTY_NOTICES.md) | [简体中文](THIRD_PARTY_NOTICES.zh-CN.md) | [README](README.zh-CN.md)

便携包含第三方组件和模型，各自的许可证与声明仍适用。项目名称不改变其归属或授权；助手原创代码尚未选择项目级开源许可证。

| 组件 | 来源与声明 |
| --- | --- |
| Vocal2Midi | [上游项目](https://github.com/Xiantaidu/Vocal2Midi)，Apache-2.0；保留 `dependencies/vocal2midi/LICENSE` 与 `ACKNOWLEDGEMENTS.md` |
| MDX 分离模型 | [UVR 模型发布](https://github.com/TRvlvr/model_repo/releases/tag/all_public_uvr_models)、[模型参数](https://github.com/TRvlvr/application_data)；模型授权与助手代码不同 |
| Python | 3.12.10，保留运行库的 `LICENSE.txt` |
| Python 库 | 保留原 `LICENSE`、`COPYING`、包内许可及元数据 |
| yt-dlp | [上游](https://github.com/yt-dlp/yt-dlp)，`vendor/` 内保留代码与许可 |
| Playwright / Node.js | [上游](https://github.com/microsoft/playwright-python)，`vendor/` 内保留原声明 |
| FFmpeg | 7.1.3-Jellyfin，启用 GPL/version3；[源码](https://github.com/jellyfin/jellyfin-ffmpeg)、[许可说明](https://ffmpeg.org/legal.html) |
| Google Chrome | 浏览器运行库；[使用条款](https://www.google.com/chrome/terms/)，开源组件见 `chrome://credits` |

Vocal2Midi 的上游资料保留 GAME、HubertFA、Qwen3-ASR、RMVPE、RomajiASR、llama.cpp、ONNX Runtime 的致谢。固定环境经过验证，不代表所有第三方文件具有相同许可证。

干净发行包不含浏览器账号、会话、私人音视频、封面、MIDI 或发布草稿。二进制包中的第三方组件仍需遵守各自的再分发条款。
