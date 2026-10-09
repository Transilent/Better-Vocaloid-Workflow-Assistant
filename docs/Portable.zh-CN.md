# Better Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md)

Windows x64 便携版。从 B站视频或本地音乐生成 MIDI，人工调音与剪辑后准备上传。

## 启动

1. 将完整 `BVWA` 文件夹解压到可写目录，例如 `D:\BVWA`。
2. 双击唯一启动入口 **Start-Assistant.bat**。
3. 在“处理音乐”选择 BV / 链接或本地音乐，选择分离方案、歌词语言，点击“开始处理”。
4. 在“任务与结果”查看 MIDI、WAV；任务中断后可“继续任务”。

侧栏包含处理音乐、模型与组件、任务与结果、发布准备。参考歌词、高级设置和日志
按需展开。调音、成品音视频对齐由人完成。VOCALOID 6 中通过“文件 → 导入”载入
MIDI，歌词选择 UTF-8。

## 可选模型

Karaoke 2 已内置。使用 BS-RoFormer：打开“模型与组件”，选择运行库，
点击“下载并安装”，完成后点击“使用此模型”。

| 运行库 | 下载量 | 预计安装占用 |
| --- | --- | --- |
| CPU | 0.46 GB | 1.56 GB |
| NVIDIA GPU / CUDA | 3.52 GB | 6.14 GB |

安装器显示所需临时空间、核对文件，并支持取消与重试。安装后可离线使用。
只保留一套已启用的可选运行库。CPU 与 CUDA 保留原分离权重，日语歌词仍使用
Romaji ASR。分离、音高和歌词需要试听校正。

## 发布准备

选择来源任务和成品视频，核对本家原始封面，编辑各平台标题与简介。
点击“生成发布资料”后，主按钮切换为“上传所选平台”。登录入口在
“账号与上传控制”中。上传后在网页检查并手动发布。

## 文件与排错

- 保留完整的 `dependencies`、`models`、`assets`、`components` 文件夹。
  换电脑时复制整个 BVWA 目录。
- 任务、账号和草稿分别存于 `jobs`、`cache`、`publish-packages`。
  每台新电脑需要重新登录平台。
- 启动错误见 `launcher.log`。
- `tools/Check-Dependencies.bat` 检查运行库完整性。
- `tools/Use-CPU.bat`、`tools/Use-GPU.bat` 切换基础分离和 MIDI 的 CPU / DirectML
  模式，切换后重启；可选模型的设备在“高级设置”单独选择。

完整指南及源码见[项目仓库](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant)。
授权说明见[第三方说明](THIRD_PARTY_NOTICES.zh-CN.md)。

## 相关项目

助手使用 [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi)、
[UVR 模型](https://github.com/TRvlvr/model_repo)、
[Pymss](https://github.com/pymss-project/pymss)、
[BS-RoFormer 权重](https://huggingface.co/becruily/bs-roformer-karaoke)、
[PyTorch](https://pytorch.org/)、[ONNX Runtime](https://github.com/microsoft/onnxruntime)、
[yt-dlp](https://github.com/yt-dlp/yt-dlp)、[FFmpeg](https://ffmpeg.org/)、
[Playwright](https://github.com/microsoft/playwright-python)、
[PyQt](https://www.riverbankcomputing.com/software/pyqt/) 及其支持库。
界面布局参考 [Pymss Studio](https://github.com/pymss-project/pymss-studio)。
原有致谢和许可随相应组件保留。
