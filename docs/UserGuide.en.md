# User guide

[English](UserGuide.en.md) | [简体中文](UserGuide.zh-CN.md) | [README](../README.md)

## Input

The portable folder includes the runtime. For a source checkout, `tools/Start-Diagnostics.bat` installs it first, using a complete portable ZIP in this folder or its parent, or downloading the Release files. Without a local archive, it downloads the complete package; its current size is listed in the Release. It verifies dependency hashes and preserves assistant settings, jobs and sign-in profiles. After an interruption, run the diagnostic script again.

Run `BVWA.exe` to start without a console window. Open **处理音乐** (Process music). Under **音乐来源**,
select **B站 BV 号 / 链接** or **本地音乐文件**. **选择文件** opens the music chooser.
A local file requires no BV and is copied before decoding. Saved snapshots allow
resume after the original is moved. The sidebar also contains **模型与组件**
(Models/components), **任务与结果** (Tasks/results), **发布准备** (Publication), and **设置与更新** (Settings/updates).

## Upgrading from v0.2.0

Version 0.3.0 uses a revised base-runtime inventory and requires the complete portable package. Close the assistant and extract the new package into a separate folder. To retain data on the same computer, copy `config.json`, `jobs/`, `publish-packages/`, `last_job.txt`, `cache/workspace.json` and `cache/publish-browser/` from the old folder when present. An installed BS-RoFormer component can be retained by copying `dependencies/optional/bs-roformer/`. Keep custom task directories in their existing locations. Avoid copying old program files or update caches over the new package. On another computer, sign in to the publishing platforms again.

The program-only update is for installations with the same base runtime. When that runtime differs, Settings/updates directs you to the complete package.

## Save location

In **输出与处理设置** (Output/processing settings), use **选择目录** (Choose folder) to set the save location, or enter a new folder path. The default is the assistant's `jobs` folder. Each task creates a separate subfolder containing the downloaded video (`video/source.mp4` for BV inputs), audio and MIDI. The assistant remembers the chosen location and earlier task folders. Resuming uses the original task folder; reprocessing creates a new task in the currently selected save location.

## Separation and transcription

| Interface option | Meaning |
| --- | --- |
| 自动分离 · 主唱 / 和声双轨 | Two-stage model separation and two-part MIDI |
| 导入外部分轨 | Use full-length lead, backing, and accompaniment WAV stems |
| 合并人声 · 单轨 MIDI | Combined vocal transcription |
| 中文 / 日语 | Chinese / Japanese lyric processing |
| 中文歌词格式 | Characters or pinyin for both Chinese vocal tracks |
| 识别歌词并写入 MIDI | Enable lyric recognition; unchecked gives notes only |
| 工作流 → 生成 MIDI → 提取精度 | Choose 8, 16 or 32 GAME steps |

External stems must come from the same source and retain the full timeline from zero, including initial silence. Mono and 48 kHz WAVs are converted. A different duration is rejected; equal duration does not prove correct alignment.

Paste reference lyrics only. Spaces, punctuation, and line breaks are removed. Lead and backing lyrics are independent. Do not paste credits or a full description. Empty backing lyrics allow independent recognition.

Expand **参考歌词（可选）** to enter lyrics. Open **工作流** to set MIDI steps
and each engine's inference device. Logs are available through **处理日志**.

## Optional BS-RoFormer component

Open Models/Components, choose a CPU or CUDA runtime and click **下载并安装**.
The download is about 0.46 GB for CPU or 3.52 GB for CUDA. Installation preserves
the base runtime and verifies SHA-256 before loading the original model. A failed
or cancelled installation does not activate a partial component. Retry to resume
verified downloads; choose **更多 → 重新安装所选运行库** to repair an existing component.

Choose **使用此模型**, or select BS-RoFormer in processing. CPU and CUDA use the
same model. **自动** chooses CUDA when available, otherwise CPU. Explicit CUDA
selection fails clearly if unavailable. Karaoke 2 remains available without this
download. Retain the complete `dependencies/optional` folder when moving the app.

Models separate musical roles, not singer identities. Listen for leakage, missed notes, and lyric errors. More inference steps do not guarantee higher accuracy.

Chinese lyrics use Qwen ASR. Japanese lyrics retain the bundled Romaji ASR model, followed by HubertFA alignment; selecting Japanese does not replace its weights with Qwen. If a chunk has empty recognition or an unusable alignment, it keeps pitch-only notes and is recorded in the alignment report. A bad alignment chunk does not discard successfully aligned chunks. Short Japanese mora repairs do not bridge pauses longer than 150 ms.

## Run and recovery

**开始处理** starts a new job. In Tasks/Results, select a task and use **继续 / 重试**
to resume its saved parameters or **用当前工作流重做** to create a new job with the
current workflow and reuse its input. **取消处理** appears while running.
Cooperative cancellation can take up to 30 seconds before forced termination.
**打开结果目录** opens the selected job folder.

Changed inputs, settings, or corrupted MIDI invalidate the affected cached result. A completed vocal part can be reused when resuming the other. Silent backing vocals produce an empty backing track with a message.

## Outputs

| File within a job | Purpose |
| --- | --- |
| `audio/source.wav` | Full decoded input: 44.1 kHz stereo float WAV |
| `audio/lead.wav`, `audio/backing.wav` | Vocal parts |
| `audio/instrumental.wav`, `audio/vocals.wav` | Accompaniment and combined vocals |
| `midi/voices.mid` | Two vocal note tracks plus tempo metadata |
| `midi/lead/lead.mid`, `midi/backing/backing.mid` | Separate MIDI imports |
| `midi/vocals.mid` | Single-part output |
| `midi/lead/lead_alignment.json`, `midi/backing/backing_alignment.json` | Chunk times, alignment coverage and fallback reasons, without lyric text |
| `midi/需要核对的音符.csv` | Time-based review hints; pitches are not automatically changed |
| `original-cover.*` | Original Bilibili cover, for BV inputs |
| `status.json` and logs | Stage state and diagnostics |

MIDI uses 120 BPM to represent time and disables quantization. This is not detected song tempo. Preserve positions in seconds if changing project tempo. Tuning and final audio/video alignment are manual.

In VOCALOID 6, create a project and use **File → Import** to import `voices.mid` or an individual vocal MIDI. **Open** is for project files. Select UTF-8 for embedded lyrics when the import dialog offers an encoding option; lyric import is supported from version 6.2. See the [official reference manual](https://rsc-net.vocaloid.com/assets/pdf_files/bb/VOCALOID_Reference_Manual_ENG.pdf) and [MIDI import support](https://www.vocaloid.com/en/support/faq/617).

## Upload preparation

1. Open **发布准备** in the sidebar. Select a source job or fetch metadata using a BV identifier.
2. Choose a finished video containing both audio and video streams.
3. Enter the cover singer and tuning credit. Apply the `【singer翻唱】song` title template, or edit the title directly.
4. Review each platform's description. Source credits are editable drafts and must be checked.
5. Fetch the original cover, explicitly choose a frame, or select an image. Local music jobs need a chosen image and manually completed source/STAFF information, or a separately fetched BV source.
6. Click **生成发布资料**. Bilibili covers preserve the full image, with padding for 4:3 and 16:9 when necessary. The primary button then changes to **上传所选平台**.
7. Expand **账号与上传控制**, sign in using each platform's dedicated browser and close its sign-in window.
8. Click **上传所选平台**. The assistant waits for upload completion and fills text/covers, leaving the browser open.
9. Review categories, tags, attribution, platform requirements, and remaining fields. Publish manually.

Regenerate the bundle after changing a finished file, cover or draft. The primary button returns to preparation when the form changes. Under each platform's **更多** menu, **继续填写当前上传** can update an open upload with the same verified video without uploading it again. **发布资料** opens or restores existing bundles. Closed browsers require a new upload. Website changes may require manual intervention or selector updates.

## CLI and diagnostics

Run from the extracted folder:

```powershell
& '.\dependencies\vocal2midi\python\python.exe' -B .\pipeline.py --source 'BV_IDENTIFIER' --voice-mode dual
& '.\dependencies\vocal2midi\python\python.exe' -B .\pipeline.py --audio-file 'D:\Music\song.mp3' --voice-mode dual
& '.\dependencies\vocal2midi\python\python.exe' -B .\pipeline.py --job 'JOB_FOLDER'
& '.\dependencies\vocal2midi\python\python.exe' -B .\separate_audio.py --input 'INPUT_AUDIO' --output 'EMPTY_OUTPUT_FOLDER'
```

`--notes-only` skips lyrics; `--midi-steps 8|16|32` selects inference steps; `--until separation` stops before MIDI. `--source`, `--audio-file` and `--job` are mutually exclusive. Local separation needs an empty output directory.

The standalone `separate_audio.py` helper is available in the source repository.
For portable CLI separation, use `pipeline.py --until separation`.

Diagnostic batch files are under `tools/` in the portable package and at the root
in a source checkout. `Check-Dependencies.bat` verifies fixed dependencies.
Startup errors appear in `launcher.log`. `Use-CPU.bat` and `Use-GPU.bat` change
base CPU/DirectML device settings; restart afterward.

`cache/` holds temporary files and dedicated sign-in profiles, `jobs/` holds audio/MIDI, and `publish-packages/` holds publication snapshots. These private runtime directories are excluded from Git and clean releases. Sign in again on each new computer.

## Downloads and program updates

Models/components offers automatic, official and accelerated download modes. Settings/updates sets the default. GitHub files use GitProxy; Hugging Face models use HF-Mirror. Other dependencies use their official sources. Automatic and accelerated modes try the alternate source after connection failures. File-size and SHA-256 checks remain enabled.

Use Settings/updates to check versions, read release notes and download program updates. Startup checks and automatic downloads are separate preferences. Once verification finishes, end active tasks and select Restart/install. Updates preserve settings, tasks, platform sign-in and optional models. Replacement failures restore the previous program. A changed base runtime requires a full portable package. Version 0.2.0 requires the complete package when upgrading to 0.3.0.

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
