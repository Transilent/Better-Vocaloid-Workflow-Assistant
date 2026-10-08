# User guide

[English](UserGuide.en.md) | [简体中文](UserGuide.zh-CN.md) | [README](../README.md)

## Input

The portable folder includes the runtime. A source checkout's launcher installs it first, using a complete portable ZIP in this folder or its parent, or downloading the Release files. This requires about 5.7 GB of network data when no local archive exists. It verifies dependency hashes and preserves assistant settings, jobs and sign-in profiles. After an interruption, run the launcher again.

Run `Start-Assistant.bat`. In **音乐来源** (Music source), select **B站 BV 号 / 链接** for a BV identifier/link, or **导入本地音乐** for a local file. **选择音乐…** opens the music chooser. A local file requires no BV and is copied before decoding; the original is not modified. Saved snapshots allow resume after the original is moved.

## Separation and transcription

| Interface option | Meaning |
| --- | --- |
| 模型全自动：人声 → 主唱 / 和声 | Two-stage model separation and two-part MIDI |
| 外部分轨：导入… | Use full-length lead, backing, and accompaniment WAV stems |
| 原版：合并人声单轨 | Combined vocal transcription |
| 中文 / 日语 | Chinese / Japanese lyric processing |
| 识别歌词并写入 MIDI | Enable lyric recognition; unchecked gives notes only |
| 精细提取（较慢） | 16 GAME steps; unchecked uses 8 |

External stems must come from the same source and retain the full timeline from zero, including initial silence. Mono and 48 kHz WAVs are converted. A different duration is rejected; equal duration does not prove correct alignment.

Paste reference lyrics only. Spaces, punctuation, and line breaks are removed. Lead and backing lyrics are independent. Do not paste credits or a full description. Empty backing lyrics allow independent recognition.

Models separate musical roles, not singer identities. Listen for leakage, missed notes, and lyric errors. More inference steps do not guarantee higher accuracy.

Chinese lyrics use Qwen ASR. Japanese lyrics retain the bundled Romaji ASR model, followed by HubertFA alignment; selecting Japanese does not replace its weights with Qwen. If a chunk has empty recognition or an unusable alignment, it keeps pitch-only notes and is recorded in the alignment report. A bad alignment chunk does not discard successfully aligned chunks. Short Japanese mora repairs do not bridge pauses longer than 150 ms.

## Run and recovery

**开始处理** starts a new job. **继续上次任务** resumes with saved parameters. **重做上次分离 / MIDI** starts a new job with current settings while reusing input assets. **取消** cancels the processing tree; cooperative model cancellation can take up to 30 seconds before forced termination. **打开结果目录** opens the job folder.

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

1. In **发布准备 / 上传**, select a source job or fetch metadata using a BV identifier.
2. Choose a finished video containing both audio and video streams.
3. Enter the cover singer and tuning credit. Apply the `【singer翻唱】song` title template, or edit the title directly.
4. Review each platform's description. Source credits are editable drafts and must be checked.
5. Fetch the original cover, explicitly choose a frame, or select an image. Local music jobs need a chosen image and manually completed source/STAFF information, or a separately fetched BV source.
6. Generate a bundle. Bilibili covers preserve the full image, with padding for 4:3 and 16:9 when necessary.
7. Sign in using each platform's dedicated browser and close its sign-in window.
8. Start upload preparation. The assistant waits for upload completion and fills text/covers, leaving the browser open.
9. Review categories, tags, attribution, platform requirements, and remaining fields. Publish manually.

Regenerate the bundle after changing a finished file or cover. **继续填写** can update an open upload with the same verified video without uploading it again. Closed browsers require a new upload. Website changes may require manual intervention or selector updates.

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
