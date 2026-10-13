# User guide

[English](UserGuide.en.md) | [简体中文](UserGuide.zh-CN.md) | [README](../README.md)

## Input

The portable folder includes the runtime. For a source checkout, `tools/Start-Diagnostics.bat` installs it first, using a complete portable ZIP in this folder or its parent, or downloading the Release files. Without a local archive, it downloads the complete package; its current size is listed in the Release. It verifies dependency hashes and preserves assistant settings, jobs and sign-in profiles. After an interruption, run the diagnostic script again.

Run `BVWA.exe` to start without a console window. Open **调音工作流** (Tuning workflow). Under **音乐来源**,
select **B站 BV 号 / 链接** or **本地音乐文件**. **选择文件** opens the music chooser.
A local file requires no BV and is copied before decoding. Saved snapshots allow
resume after the original is moved. The sidebar also contains **模型与组件**
(Models/components), **任务与结果** (Tasks/results), **发布工作流** (Publishing workflow), and **设置与更新** (Settings/updates).

## Upgrading

v0.3.1 and v0.4.0 can upgrade to v0.4.1 in **Settings/updates**. Check, download and restart to install. The migration window downloads the delta matching the existing runtime, preserving model weights, tasks, settings, sign-in profiles and optional components. It retains your download-source preference and supports GitProxy, resumable verified downloads and rollback. The same legacy path supports v0.3.0.

v0.4.1 provides update archives only. New installations should use the v0.4.0 complete portable package first. Individual update ZIPs cannot run as standalone installations.

## Save location

In **音频导出 → 保存目录** (Audio export → Save folder), use **选择目录** (Choose folder) to set the save location, or enter a new folder path. The default is the assistant's `jobs` folder. Each task creates a separate subfolder containing the downloaded video (`video/source.mp4` for BV inputs), audio and MIDI. The assistant remembers the chosen location and earlier task folders. Resuming uses the original task folder; reprocessing creates a new task in the currently selected save location.

## Separation and transcription

| Interface option | Meaning |
| --- | --- |
| 自动分离 · 主唱 / 和声双轨 | Two-stage model separation and two-part MIDI |
| 导入外部分轨 | Use full-length lead, backing, and accompaniment WAV stems |
| 合并人声 · 单轨 MIDI | Combined vocal transcription |
| 中文 / 日语 | Chinese / Japanese lyric processing |
| 中文歌词格式 | Characters or pinyin for both Chinese vocal tracks |
| 识别歌词并写入 MIDI | Enable lyric recognition; unchecked gives notes only |
| 调音工作流 → MIDI 与工程 → 提取精度 | Choose 8, 16 or 32 GAME steps |

External stems must come from the same source and retain the full timeline from zero, including initial silence. Mono and 48 kHz WAVs are converted. A different duration is rejected; equal duration does not prove correct alignment.

Paste reference lyrics only. Spaces, punctuation, and line breaks are removed. Lead and backing lyrics are independent. Do not paste credits or a full description. Empty backing lyrics allow independent recognition.

Expand **参考歌词与校准** to search NetEase or import/edit local reference lyrics. Set extraction precision under **MIDI 与工程**, and devices in the top toolbar's **设备设置**. Logs are available through **处理日志**.

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

Models/components includes **中文歌词识别 → 校验 / 恢复中文模型** (Chinese lyrics → Verify/restore Chinese model). It verifies the original Qwen weights and downloads missing or damaged files. The complete runtime includes these approximately 4.38 GiB of weights. Japanese and notes-only extraction check their own dependencies and can run without Qwen. Cache cleanup does not remove installed weights.

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
| `exports/voices.ustx`, `voices.vsqx`, `voices.svp` | Editor projects, with optional pitch curves |
| `exports/audio/` | Selected compressed stem formats; WAV uses `audio/` |

MIDI uses 120 BPM to represent time and disables quantization. This is not detected song tempo. Preserve positions in seconds if changing project tempo. Tuning and final audio/video alignment are manual.

In VOCALOID 6, create a project and use **File → Import** to import `voices.mid` or an individual vocal MIDI. **Open** is for project files. Select UTF-8 for embedded lyrics when the import dialog offers an encoding option; lyric import is supported from version 6.2. See the [official reference manual](https://rsc-net.vocaloid.com/assets/pdf_files/bb/VOCALOID_Reference_Manual_ENG.pdf) and [MIDI import support](https://www.vocaloid.com/en/support/faq/617).

## Upload preparation

Open **发布工作流** (Publishing workflow), directly below Tuning workflow. Each page shows one step:

1. **Finished video/source:** select the finished video and its source task, or fetch source information from a BV identifier.
2. **Cover:** preview the original cover at a large size, choose a custom image, or capture a video frame. Local music tasks require a custom image unless BV source information is added.
3. **Title/description:** review both platforms side by side, apply the singer/title template and check credits and character limits.
4. **Account/upload:** select **生成发布资料** (Prepare assets), then sign in and select **上传所选平台** (Upload selected platforms). The main action stays visible at the bottom while the page scrolls.

Use Previous/Next or the numbered steps to move between pages. Next checks the required inputs before advancing. Each platform has visible sign-in, resume and close controls; unavailable actions are disabled. Close the sign-in window after logging in. The assistant leaves creator pages open for review and manual publishing.

Original covers come from video metadata. Frame capture and custom images are separate choices. Cover conversion preserves the complete image with padding for 4:3 and 16:9 when needed.

Regenerate publication files after changing a finished video, cover or draft. **继续填写当前上传** (Continue current upload) updates an open task with the same verified video. Closed browsers require a new upload. Review categories, tags, credits and remaining required fields on the creator page before publishing.

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

Diagnostic batch files are under `tools/`. `Check-Dependencies.bat` verifies fixed dependencies.
Startup errors appear in `launcher.log`. `Use-CPU.bat` and `Use-GPU.bat` change
base CPU/DirectML device settings; restart afterward.

`cache/` holds temporary files and dedicated sign-in profiles, `jobs/` holds audio/MIDI, and `publish-packages/` holds cover assets, publication text and a verified reference to the finished video. The complete finished video is not copied. Keep it in place until upload; regenerate publication assets after moving or changing it. These private runtime directories are excluded from Git and clean releases. Sign in again on each new computer.

## Downloads and program updates

Models/components offers automatic, official and accelerated download modes. Settings/updates sets the default. GitHub files use GitProxy; Hugging Face models use HF-Mirror. Other dependencies use their official sources. Automatic and accelerated modes try the alternate source after connection failures. File-size and SHA-256 checks remain enabled.

Automatic updates run only at startup. By default, the assistant checks for a compatible program update, downloads and verifies it, then restarts after a 10-second countdown. You can postpone installation or disable these preferences in Settings/updates. Once you start using the assistant, installation is deferred until the next launch. There are no periodic checks during normal use. Manually checked or downloaded updates require you to select Restart/install. Updates preserve settings, tasks, platform sign-in and optional models. Replacement failures restore the previous program. A changed base runtime requires a full portable package. Version 0.2.0 requires the complete package when upgrading to 0.3.0 or later.

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

WAV export uses the task's existing audio files. Compressed formats are stored separately. Continuing a task validates inputs and outputs before reuse; damaged exports are repaired individually. Reading source credits and the original cover does not download the full video. Video is retrieved when needed for frame capture or source-video access.

The **参考歌词与校准** (Reference lyrics/calibration) card sits after Separation. Choose **NetEase matching** to search by song and artist or paste a song ID/link; the selected original lyrics fill the lead vocal by default. Choose **Local matching** to import LRC/YRC/TXT reference lyrics or edit them directly. Empty fields use local recognition for that voice. Lead text is kept separately for each source, and backing lyrics are never overwritten by a NetEase lead match. Reference text is aligned against vocal audio; song timestamps do not move MIDI notes.

The **歌词转字幕** (Lyrics/subtitles) card has two inputs: **NetEase matching** uses an existing song lyric timeline, while **MIDI matching** uses lyric events and the MIDI tempo map. Select a vocal track when importing a multi-track MIDI. Each input keeps its own editable draft. Preview and adjust cue text/times and the overall offset before exporting UTF-8 SRT or WebVTT. Untimed plain text is rejected; its duration is never divided into estimated subtitles. Subtitle matching does not replace the transcription reference lyrics.
