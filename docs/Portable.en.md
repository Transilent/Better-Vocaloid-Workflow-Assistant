# Better Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md)

Windows x64 portable edition. Prepare MIDI from a Bilibili video or local music,
then prepare creator uploads after manual tuning and video editing.

## Start

1. Extract the complete `BVWA` folder to a writable location, such as `D:\BVWA`.
2. Double-click **BVWA.exe**. The executable starts the application without a console window.
3. Open **调音工作流** (Tuning workflow). Choose a BV/link or local music file,
   choose the separation mode and lyric language, then select **开始处理**.
4. Find MIDI and WAV outputs under **任务与结果** (Tasks/results).
   Use **继续任务** to resume an interrupted task.

The interface uses Chinese labels. The sidebar contains Tuning workflow,
Publishing workflow, Models/components, Tasks/results and Settings/updates.
Expand a workflow step to edit its options; use Device settings in the toolbar
for inference devices. Tuning and final video/audio alignment are manual.
In VOCALOID 6, use File → Import for MIDI and UTF-8 for lyrics.

## Upgrading

v0.3.0 and v0.3.1 support online upgrading to v0.4.0. In **Settings/updates**, check for updates, download the update and restart to install. The first restart opens a short migration window that downloads only new dependencies and adapted code, reuses the installed models, then restarts into v0.4.0. It supports GitHub direct downloads and GitProxy, verified resume and rollback. Settings, tasks, platform sessions and optional models remain in place. Later updates use the program package matching the installed runtime.

For a fresh installation, use the complete portable package. Keep a backup of working editor projects before any upgrade. The GitHub Source code archives and individual update ZIPs are not complete portable installations.

## Save location

In **音频导出 → 保存目录** (Audio export → Save folder), use **选择目录** (Choose folder) to set the save location, or enter a new folder path. The default is the assistant's `jobs` folder. Each task creates a separate subfolder containing the downloaded video (`video/source.mp4` for BV inputs), audio and MIDI. The assistant remembers the chosen location and earlier task folders. Resuming uses the original task folder; reprocessing creates a new task in the currently selected save location.

## Lyrics and updates

New tasks use Vocal2Midi 2.0 without a backend selector. Existing tasks resume with their recorded processing settings.

For Chinese, choose **汉字** (characters) or **拼音** (pinyin) under **中文歌词格式**. The choice applies to both vocal tracks and is saved with the task.

Automatic updates run only at startup: check, download, verify, then restart after a 10-second countdown. Starting to use the assistant or postponing installation defers it to the next launch. Normal use has no periodic checks. Open **设置与更新** (Settings/updates) to change these preferences, select a download source or check manually. Manually downloaded updates require Restart/install. Program updates preserve settings, tasks, platform sign-in and installed models.

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

Open **发布工作流** (Publishing workflow), directly below Tuning workflow. Each page shows one step:

1. **Finished video/source:** select the finished video and its source task, or fetch source information from a BV identifier.
2. **Cover:** preview the original cover at a large size, choose a custom image, or capture a video frame. Local music tasks require a custom image unless BV source information is added.
3. **Title/description:** review both platforms side by side, apply the singer/title template and check credits and character limits.
4. **Account/upload:** select **生成发布资料** (Prepare assets), then sign in and select **上传所选平台** (Upload selected platforms). The main action stays visible at the bottom while the page scrolls.

Use Previous/Next or the numbered steps to move between pages. Next checks the required inputs before advancing. Each platform has visible sign-in, resume and close controls; unavailable actions are disabled. Close the sign-in window after logging in. The assistant leaves creator pages open for review and manual publishing.

Original covers come from video metadata. Frame capture and custom images are separate choices. Cover conversion preserves the complete image with padding for 4:3 and 16:9 when needed.

Regenerate publication files after changing a finished video, cover or draft. **继续填写当前上传** (Continue current upload) updates an open task with the same verified video. Closed browsers require a new upload. Review categories, tags, credits and remaining required fields on the creator page before publishing.

## Files and troubleshooting

- Keep the complete `dependencies`, `models`, `assets` and `components` folders.
  Move the whole BVWA folder when transferring to another computer.
- Private jobs, sign-in profiles and drafts are created under `jobs`, `cache`
  and `publish-packages`. Each new computer needs fresh platform sign-in.
- Startup errors are recorded in `launcher.log`; `tools/Start-Diagnostics.bat` provides visible diagnostics.
- `tools/Check-Dependencies.bat` checks runtime integrity.
- `tools/Use-CPU.bat` and `tools/Use-GPU.bat` select CPU/DirectML for the base
  separation and MIDI pipeline; restart afterward. The optional model's device
  is selected in the workflow toolbar's Device settings.

Full guides and source: [project repository](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant).
Third-party terms: [notices](THIRD_PARTY_NOTICES.md).

## Workflows, tasks and recovery

The **调音工作流** (Tuning workflow) page contains six expandable steps: Music source, Separation, Reference lyrics/calibration, MIDI/projects, Audio export and Lyrics/subtitles. Expanded controls use the full content width, with larger text and click targets. Open **设备设置** (Device settings) in the top toolbar to choose each engine's device. Presets save language, models, devices and output formats, without song paths or reference lyrics.

The sidebar order is Tuning workflow, Publishing workflow, Models/components, Tasks/results and Settings/updates. Choose the light or dark theme at its bottom. The light theme uses a soft grey-green background, white cards and a restrained green accent. The dark theme uses solid charcoal-green surfaces. Standard window controls are retained.

Device settings also provides a shared **DirectML 显卡** (DirectML adapter) selector and **CPU 线程数** (CPU threads). Auto prefers a dedicated graphics adapter and keeps each engine's thread defaults. An explicit thread limit applies to the relevant CPU operations; more threads may not improve speed. These choices are saved with presets.

Disable MIDI/projects for an audio-only task. Project export can be switched off independently of MIDI. USTX, VSQX and SVP support optional RMVPE pitch curves and accompaniment references. Audio exports support WAV, FLAC, MP3 and OGG; WAV outputs reuse the task audio. Render the synthesized singing voice in your editor.

When any stem exceeds full scale, FLAC, MP3 and OGG exports share peak attenuation to preserve the balance between vocals and accompaniment. The applied gain is recorded in `exports/report.json`. Original float WAV files retain their full signal.

**任务与结果** (Tasks/results) offers search, filters, renaming, logs and retry actions. Continuing uses the task's recorded parameters; **用当前工作流重做** creates a new task with the current preset. Progress shows completed stages, elapsed time and measurable subtasks. Validated outputs are reused. Failed device operations offer a CPU retry.

## Disk management and subtitles

Before regenerating an output that has changed since export, the assistant preserves it under the task’s `backups/` folder and records the path in its report. This protects in-place MIDI and project edits. Unchanged generated files do not create backups. Keep your working editor project in a separate file when possible.

Open **设置与更新 → 缓存与磁盘** (Settings/updates → Cache/disk), scan usage and select disposable files to clean. Task outputs, model weights, presets and sign-in sessions are protected. Downloads and unfinished range segments can be cleaned after the active operation ends.

WAV export uses the task's existing audio files; compressed formats are stored separately. Continuing validates outputs and repairs damaged exports individually. Keep the finished video in its original location until upload, since publication assets store a verified reference rather than a full copy. Models/components can verify and restore the original Chinese Qwen weights (approximately 4.38 GiB); Japanese and notes-only extraction can run independently.

The **参考歌词与校准** (Reference lyrics/calibration) card sits after Separation. Choose **NetEase matching** to search by song and artist or paste a song ID/link; the selected original lyrics fill the lead vocal by default. Choose **Local matching** to import LRC/YRC/TXT reference lyrics or edit them directly. Empty fields use local recognition for that voice. Lead text is kept separately for each source, and backing lyrics are never overwritten by a NetEase lead match. Reference text is aligned against vocal audio; song timestamps do not move MIDI notes.

The **歌词转字幕** (Lyrics/subtitles) card has two inputs: **NetEase matching** uses an existing song lyric timeline, while **MIDI matching** uses lyric events and the MIDI tempo map. Select a vocal track when importing a multi-track MIDI. Each input keeps its own editable draft. Preview and adjust cue text/times and the overall offset before exporting UTF-8 SRT or WebVTT. Untimed plain text is rejected; its duration is never divided into estimated subtitles. Subtitle matching does not replace the transcription reference lyrics.


Large component and update downloads use up to 8 concurrent connections where HTTP Range is supported, with resumable segments and SHA-256 verification. Unsupported servers fall back to one connection. GitHub uploads use the official endpoint directly.

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
