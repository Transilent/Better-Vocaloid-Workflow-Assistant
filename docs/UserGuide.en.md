# User guide

[English](UserGuide.en.md) | [简体中文](UserGuide.zh-CN.md) | [README](../README.md)

## Input

The portable folder includes the runtime. A source checkout's launcher installs it first, using a complete portable ZIP in this folder or its parent, or downloading the Release files. This requires about 5.7 GB of network data when no local archive exists. It verifies dependency hashes and preserves assistant settings, jobs and sign-in profiles. After an interruption, run the launcher again.

Run `Start-Assistant.bat`. Open **处理音乐** (Process music). Under **音乐来源**,
select **B站 BV 号 / 链接** or **本地音乐文件**. **选择文件** opens the music chooser.
A local file requires no BV and is copied before decoding. Saved snapshots allow
resume after the original is moved. The sidebar also contains **模型与组件**
(Models/components), **任务与结果** (Tasks/results), and **发布准备** (Publication).

## Separation and transcription

| Interface option | Meaning |
| --- | --- |
| 自动分离 · 主唱 / 和声双轨 | Two-stage model separation and two-part MIDI |
| 导入外部分轨 | Use full-length lead, backing, and accompaniment WAV stems |
| 合并人声 · 单轨 MIDI | Combined vocal transcription |
| 中文 / 日语 | Chinese / Japanese lyric processing |
| 识别歌词并写入 MIDI | Enable lyric recognition; unchecked gives notes only |
| 精细提取 MIDI（较慢）, in 高级设置 | 16 GAME steps; unchecked uses 8 |

External stems must come from the same source and retain the full timeline from zero, including initial silence. Mono and 48 kHz WAVs are converted. A different duration is rejected; equal duration does not prove correct alignment.

Paste reference lyrics only. Spaces, punctuation, and line breaks are removed. Lead and backing lyrics are independent. Do not paste credits or a full description. Empty backing lyrics allow independent recognition.

Expand **参考歌词（可选）** to enter lyrics. Expand **高级设置** for MIDI steps
and the optional model's inference device. Logs are available through **处理日志**.

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

**开始处理** starts a new job. In Tasks/Results, select a task and use **继续任务**
to resume its saved parameters or **重新分离 / MIDI** to create a new job with the
current processing settings and reuse its input. **取消处理** appears while running.
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

`Check-Dependencies.bat` verifies fixed dependencies. Startup errors appear in `launcher.log`. `Use-CPU.bat` and `Use-GPU.bat` change device settings; restart afterward.

`cache/` holds temporary files and dedicated sign-in profiles, `jobs/` holds audio/MIDI, and `publish-packages/` holds publication snapshots. These private runtime directories are excluded from Git and clean releases. Sign in again on each new computer.
