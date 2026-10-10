# Better Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md)

Windows x64 portable edition. Prepare MIDI from a Bilibili video or local music,
then prepare creator uploads after manual tuning and video editing.

## Start

1. Extract the complete `BVWA` folder to a writable location, such as `D:\BVWA`.
2. Double-click **BVWA.exe**. The executable starts the application without a console window.
3. Open **处理音乐** (Process music). Choose a BV/link or local music file,
   choose the separation mode and lyric language, then select **开始处理**.
4. Find MIDI and WAV outputs under **任务与结果** (Tasks/results).
   Use **继续任务** to resume an interrupted task.

The interface uses Chinese labels. The sidebar contains processing,
models/components, task results, publication, workflows, storage, subtitles and settings/updates. Reference lyrics
and logs expand when needed; devices and precision are set in workflow nodes. Tuning and final video/audio alignment
are manual. In VOCALOID 6, use File → Import for MIDI and UTF-8 for lyrics.

## Upgrading from v0.2.0

Version 0.3.0 uses a revised base-runtime inventory and requires the complete portable package. Close the assistant and extract the new package into a separate folder. To retain data on the same computer, copy `config.json`, `jobs/`, `publish-packages/`, `last_job.txt`, `cache/workspace.json` and `cache/publish-browser/` from the old folder when present. An installed BS-RoFormer component can be retained by copying `dependencies/optional/bs-roformer/`. Keep custom task directories in their existing locations. Avoid copying old program files or update caches over the new package. On another computer, sign in to the publishing platforms again.

The program-only update is for installations with the same base runtime. When that runtime differs, Settings/updates directs you to the complete package.

## Save location

In **输出与处理设置** (Output/processing settings), use **选择目录** (Choose folder) to set the save location, or enter a new folder path. The default is the assistant's `jobs` folder. Each task creates a separate subfolder containing the downloaded video (`video/source.mp4` for BV inputs), audio and MIDI. The assistant remembers the chosen location and earlier task folders. Resuming uses the original task folder; reprocessing creates a new task in the currently selected save location.

## Lyrics and updates

For Chinese, choose **汉字** (characters) or **拼音** (pinyin) under **中文歌词格式**. The choice applies to both vocal tracks and is saved with the task.

Open **设置与更新** (Settings/updates) to check for new versions and choose official or accelerated downloads. Startup checks and automatic downloads are configurable. After verification, restart to install. Program updates preserve settings, tasks, platform sign-in and installed models.

GitHub files support GitProxy; Hugging Face models support HF-Mirror. All downloaded packages are verified using SHA-256.

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
- Startup errors are recorded in `launcher.log`; `tools/Start-Diagnostics.bat` provides visible diagnostics.
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

## Workflows, tasks and recovery

Select a built-in or saved preset from **工作流预设** at the top of **处理音乐** (Process music), add a song, and click **开始处理**. The selected graph, model, language, lyric format and devices are applied together. To edit a workflow, open **工作流**, save it, and click **用于处理音乐** to return. Preset selections stay synchronized between both pages.

Drag modules from the palette. Moving a module near a compatible neighbor shows a connection preview; releasing it snaps the modules into alignment and connects them. Pull a module away and release to disconnect. Double-clicking a palette item appends it after its predecessor. Click a module to edit its parameters; right-click to remove a module or connection. **整理画布** arranges modules in processing order and connects adjacent compatible modules. Ctrl + wheel zooms.

The supported sequence is **Music input → Separation → MIDI → Subtitles**. Ending at Separation exports audio without running MIDI inference. The subtitle node exports SRT and WebVTT from MIDI lyric events. Incomplete or invalid connections are rejected before creating a task. Input files, BV links and reference lyrics are entered on the processing page.

Select a node to change language, model, lyric format, extraction precision or device. The base separation and MIDI engines offer CPU/DirectML; the optional BS-RoFormer engine offers Auto/CPU/CUDA. Current settings and named presets are saved locally. Presets contain processing parameters and the graph, without song paths or reference lyrics. Existing tasks retain their recorded settings; **用当前工作流重做** creates a new task using the current workflow and the original input.

**任务与结果** supports search, status filters, sorting and renaming. The task menu can open logs, send MIDI lyrics to the subtitle editor, or delete a task after confirmation. Deleting a task removes its files permanently. A run interrupted by an application exit is shown as interrupted and can be resumed.

Progress shows the active stage, voice or model subtask, completed stages and elapsed time. Measurable subtasks show processed chunks or download size/speed; stage counts are not estimates of remaining processing time. Failures show a readable explanation and a retry action. Device failures also offer **改用 CPU 继续**; disk-space failures link to disk management. Completed stages are reused when their outputs remain valid.

## Disk management and subtitles

In **缓存与磁盘**, scan disk usage, select cache or temporary-file entries, and confirm cleanup. Tasks, installed models, sign-in sessions and presets are protected. Cleanup waits until processing, component installation and upload preparation have finished. Removing task temporary files may require regenerating them on retry.

**歌词转字幕** imports UTF-8 LRC, TXT, timed CSV (`start,end,text` in seconds), and MIDI lyric events. Preview and edit each cue, apply a global time offset, then export UTF-8 SRT or WebVTT. LRC timestamps are preserved, including repeated timestamps and offset tags. Empty timestamped lines end the preceding cue. A final line without an end marker defaults to three seconds. MIDI phrase boundaries are inferred from lyric pauses. Plain text has no timing: the tool distributes lines across a user-specified duration as an initial estimate and requires manual checking.

The portable inventory excludes development installers, third-party tests, unused Qt Quick resources and an alternate HFA export that the pipeline never loads. Active Chinese/Japanese ASR and pitch/alignment weights remain unchanged. The current inventory saves approximately 447 MB of extracted space; the compressed size is measured when an approved release is built.
