# Better Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md)

Windows x64 portable edition. Prepare MIDI from a Bilibili video or local music,
then prepare creator uploads after manual tuning and video editing.

## Start

1. Extract the complete `BVWA` folder to a writable location, such as `D:\BVWA`.
2. Double-click **Start-Assistant.bat**, the application launcher.
3. Open **处理音乐** (Process music). Choose a BV/link or local music file,
   choose the separation mode and lyric language, then select **开始处理**.
4. Find MIDI and WAV outputs under **任务与结果** (Tasks/results).
   Use **继续任务** to resume an interrupted task.

The interface uses Chinese labels. The sidebar contains processing,
models/components, task results and publication. Reference lyrics, advanced
settings and logs expand when needed. Tuning and final video/audio alignment
are manual. In VOCALOID 6, use File → Import for MIDI and UTF-8 for lyrics.

## Optional model

Karaoke 2 is included. For BS-RoFormer, open **模型与组件** (Models/components),
choose a runtime and select **下载并安装** (Download/install), then
**使用此模型** (Use model).

| Runtime | Download | Approximate installed space |
| --- | --- | --- |
| CPU | 0.46 GB | 1.56 GB |
| NVIDIA GPU / CUDA | 3.52 GB | 6.14 GB |

The installer shows required temporary space, verifies files and supports
cancel/retry. After installation, the model works offline. Only one optional
runtime is retained. CPU and CUDA use the original model weights; Japanese
lyrics retain Romaji ASR. Listen and correct separation, pitches and lyrics.

## Publication

In **发布准备** (Publication), select the source task and finished video, review
the original cover, and edit each platform's title and description. Select
**生成发布资料** (Prepare assets). The primary button changes to
**上传所选平台** (Upload selected platforms). Account sign-in controls are under
**账号与上传控制**. Review the creator page and publish manually.

## Files and troubleshooting

- Keep the complete `dependencies`, `models`, `assets` and `components` folders.
  Move the whole BVWA folder when transferring to another computer.
- Private jobs, sign-in profiles and drafts are created under `jobs`, `cache`
  and `publish-packages`. Each new computer needs fresh platform sign-in.
- Startup errors are recorded in `launcher.log`.
- `tools/Check-Dependencies.bat` checks runtime integrity.
- `tools/Use-CPU.bat` and `tools/Use-GPU.bat` select CPU/DirectML for the base
  separation and MIDI pipeline; restart afterward. The optional model's device
  is selected separately in Advanced settings.

Full guides and source: [project repository](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant).
Third-party terms: [notices](THIRD_PARTY_NOTICES.md).

## Related projects

This assistant uses [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi),
[UVR models](https://github.com/TRvlvr/model_repo),
[Pymss](https://github.com/pymss-project/pymss),
[BS-RoFormer karaoke weights](https://huggingface.co/becruily/bs-roformer-karaoke),
[PyTorch](https://pytorch.org/), [ONNX Runtime](https://github.com/microsoft/onnxruntime),
[yt-dlp](https://github.com/yt-dlp/yt-dlp), [FFmpeg](https://ffmpeg.org/),
[Playwright](https://github.com/microsoft/playwright-python),
[PyQt](https://www.riverbankcomputing.com/software/pyqt/) and their supporting
libraries. [Pymss Studio](https://github.com/pymss-project/pymss-studio) provided
the workspace design reference. Original acknowledgements and licenses are
retained with their components.
