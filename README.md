<p align="center"><img src="assets/app.svg" width="80" alt="VWA icon"></p>

# VWA — Vocaloid Workflow Assistant

术力口工作流助手 (VWA)

[English](README.md) | [简体中文](README.zh-CN.md)

A desktop assistant for preparing vocal-synthesis projects on Windows. Import a song or a Bilibili video, separate its vocal parts, and export MIDI with aligned lyrics. After tuning, prepare the finished video's cover, credits and creator uploads.

Use **Preparation → Tuning → Assemble video → Publishing** to work from a song to a finished upload. The [Moonlight workspace](docs/Frontend.en.md) supports light/dark themes, solid or translucent panels, and interface scaling. Start the app with **`VWA.exe`**.


## Features

- Bilibili BV/link input and local audio import.
- Vocals/accompaniment separation, with separate lead and backing-vocal MIDI tracks.
- Built-in Karaoke 2 and an optional BS-RoFormer component for CPU or NVIDIA GPU.
- Vocal2Midi 2.0 with Chinese and Japanese lyrics; Chinese output can use characters or pinyin.
- Resumable tasks, reference lyrics and full-length external WAV stems.
- Expandable workflow cards, saved presets and per-engine CPU/GPU settings.
- Searchable task history, stage progress, recovery actions and cache management.
- NetEase lyric search/import, editable timings and SRT/WebVTT output.
- Original video covers, editable credits, and Bilibili/Xiaohongshu upload preparation.
- A console-free Windows launcher (`VWA.exe`) and verified program updates.

The tuning workflow saves a separate SVP candidate. Render the singing voice in your editor, then optionally align your Mixdown with a video. Finished videos can go straight to Publishing. Review the audio, notes and lyrics before use; confirm the final publication on the platform website.

[Reference expression for SVP](docs/ReferenceDynamics.en.md) can add editable Loudness, Tension, Breathiness and Voicing curves. Reference mapping is optional and experimental. Timbre controls require the optional WORLD analyzer.

The [expression research notes](docs/research/SVExpression.en.md) explain the tuning references, public datasets and limits of transferring acoustic measurements to SV controls.

See [optional workflows](docs/OptionalWorkflows.en.md) for video assembly and independent lead extraction. [Voicebank calibration](docs/ReferenceDynamics.en.md#measured-voicebank-response-fitting-development-experiment) requires a measured response for the chosen voicebank.

[Notes and continuous pitch](docs/TranscriptionQuality.en.md) covers syllable boundaries, reference-lyric alignment and measured pitch export. Recognition results still need review.

## Getting started

1. Download the complete portable package from [Releases](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases): `VWA-Windows-x64.zip` for the full package, or `VWA-Windows-x64-JPN.zip` for Japanese recognition without the Chinese Qwen weights. GitHub **Source code** archives do not include the runtime.
2. If the package is split, keep all parts with the matching `portable-metadata-full.json` or `portable-metadata-jpn.json` and use the merge helper supplied with that package.
3. Extract the archive to a writable location. The extracted top-level folder is `VWA`; keep its contents together.
4. Open `VWA` and start **`VWA.exe`**.
5. Open Preparation, add music, review lyrics, notes and outputs, then select Start preparation.
6. Set the interface language in **Settings and updates → Appearance → Interface language**. Chinese, Japanese and English are available; changes apply on the next launch.

## Upgrading

Open **Settings and updates**, check for updates, then download the matching update and restart to install. Available upgrade routes are listed in each release. The assistant reuses installed models and preserves settings, tasks, sign-in sessions and optional components. Downloads support alternative sources, verification, resume and rollback. See the [update guide](docs/UpdateProtocol.en.md) when an older installation needs the compatibility helper.

For older installations, follow the release instructions or use `VWA-Updater.exe` when provided. Update archives are not complete installations.

For a fresh installation, use the complete portable package. Keep a backup of working editor projects before any upgrade. The GitHub Source code archives and individual update ZIPs are not complete portable installations.

## Save location

In **音频导出 → 保存目录** (Audio export → Save folder), use **选择目录** (Choose folder) to set the save location, or enter a new folder path. The location applies to the whole task, even when audio export is disabled. The default is the assistant's `jobs` folder. Each task creates a separate subfolder containing the downloaded video (`video/source.mp4` for BV inputs), audio and MIDI. The assistant remembers the chosen location and earlier task folders. Resuming uses the original task folder; reprocessing creates a new task in the currently selected save location.

## Lyrics and output

New tasks use Vocal2Midi 2.0 without a backend selector. Existing tasks resume with their recorded processing settings.

With Chinese selected, **中文歌词格式** chooses **汉字** (characters) or **拼音** (pinyin). The selected format applies to both vocal tracks and is saved with the task. Disable **识别歌词并写入 MIDI** to export notes without lyrics. The Chinese format setting does not affect Japanese transcription.

Reference lyrics are optional. Enter lead and backing lyrics separately; include only the sung text. Find completed files in **任务与结果** (Tasks/results).

| File | Content |
| --- | --- |
| `midi/voices.mid` | Lead and backing vocal tracks on a shared timeline |
| `midi/lead/lead.mid`, `midi/backing/backing.mid` | Individual vocal parts |
| `midi/vocals.mid` | Combined-vocal mode output |
| `audio/lead.wav`, `audio/backing.wav` | Separated vocal parts |
| `audio/instrumental.wav` | Accompaniment |

In VOCALOID 6, use **File → Import** for MIDI and select UTF-8 when a lyric-encoding option is provided.

In **MIDI 与工程 → 手动指定 BPM** (MIDI/projects → Manual BPM), set a constant tempo from 20 to 400 BPM, including decimals. MIDI, USTX, VSQX and SVP use that tempo while retaining the actual timing of notes, pitch curves and accompaniment. The choice is saved in presets and new tasks. When disabled, the assistant uses the existing 120 BPM time reference; it does not detect song tempo. This setting does not stretch audio or add a tempo map for songs with changing speed. Existing tasks resume with their recorded tempo; use **用当前工作流重做** to apply a new BPM.

## Workflows, tasks and recovery

Preparation groups music input, processing, lyrics/notes and output choices on one page. Keep output choices compact and expand lyric or note settings as needed. Device settings selects each engine’s device. Presets retain processing options without storing song paths or reference lyrics.

The workflow order is **Preparation → Tuning → Assemble video → Publishing**. Tuning accepts a task project or an imported SVP and saves a recoverable candidate. Choose one primary parameter source, optionally add a style and pitch gestures, then choose curve density. See [Tuning workflow](docs/TuningWorkflow.en.md) for supported formats and recovery. New preparation tasks defer expression processing to Tuning; existing tasks retain their recorded settings.

Models, Tasks and results, and Settings provide model installation, task management, appearance and updates. Choose light or dark, Moonlight glass or solid reading, and a suitable interface scale in Settings. The window uses standard system controls.

Device settings also provides a shared **DirectML 显卡** (DirectML adapter) selector and **CPU 线程数** (CPU threads). Auto prefers a dedicated graphics adapter and keeps each engine's thread defaults. An explicit thread limit applies to the relevant CPU operations; more threads may not improve speed. These choices are saved with presets.

Disable MIDI/projects for an audio-only task. Project export can be switched off independently of MIDI. USTX, VSQX and SVP support optional RMVPE pitch curves and accompaniment references. Audio exports support WAV, FLAC, MP3 and OGG; WAV outputs reuse the task audio. Render the synthesized singing voice in your editor.

When any stem exceeds full scale, FLAC, MP3 and OGG exports share peak attenuation to preserve the balance between vocals and accompaniment. The applied gain is recorded in `exports/report.json`. Original float WAV files retain their full signal.

**任务与结果** (Tasks/results) offers search, filters, renaming, logs and retry actions. Continuing uses the task's recorded parameters; **用当前工作流重做** creates a new task with the current preset. Progress shows completed stages, elapsed time and measurable subtasks. Validated outputs are reused. Failed device operations offer a CPU retry.

## Disk management and subtitles

Before regenerating an output that has changed since export, the assistant preserves it under the task’s `backups/` folder and records the path in its report. This protects in-place MIDI and project edits. Unchanged generated files do not create backups. Keep your working editor project in a separate file when possible.

Open **设置与更新 → 缓存与磁盘** (Settings/updates → Cache/disk), scan usage and select disposable files to clean. Task outputs, model weights, presets and sign-in sessions are protected. Downloads and unfinished range segments can be cleaned after the active operation ends.

WAV export uses the task's existing audio files. Compressed formats are stored separately. Continuing a task validates inputs and outputs before reuse; damaged exports are repaired individually. Publication assets retain a verified reference to the finished video, avoiding a second video copy. Keep that file in place until upload, and regenerate assets after moving or changing it.

The **参考歌词与校准** (Reference lyrics/calibration) card sits after Separation. Choose **NetEase matching** to search by song and artist or paste a song ID/link; the selected original lyrics fill the lead vocal by default. Choose **Local matching** to import LRC/YRC/TXT reference lyrics or edit them directly. Empty fields use local recognition for that voice. Lead text is kept separately for each source, and backing lyrics are never overwritten by a NetEase lead match. Reference text is aligned against vocal audio; song timestamps do not move MIDI notes.

The **歌词转字幕** (Lyrics/subtitles) card has two inputs: **NetEase matching** uses an existing song lyric timeline, while **MIDI matching** uses lyric events and the MIDI tempo map. Select a vocal track when importing a multi-track MIDI. Each input keeps its own editable draft. Preview and adjust cue text/times and the overall offset before exporting UTF-8 SRT or WebVTT. Untimed plain text is rejected; its duration is never divided into estimated subtitles. Subtitle matching does not replace the transcription reference lyrics.

## Optional model and download sources

Open **模型与组件** (Models/components), choose the runtime and download source, then select **下载并安装** (Download/install). After installation, select **使用此模型** (Use model).

| Runtime | Download | Installed space, approximately |
| --- | --- | --- |
| CPU | 0.46 GB | 1.56 GB |
| NVIDIA GPU / CUDA | 3.52 GB | 6.14 GB |

The installer supports cancellation, resume and SHA-256 verification. The original BS-RoFormer checkpoint is used for both lyric languages. Japanese transcription retains the existing Romaji ASR model.

Open **Models → Chinese lyric recognition** to download the original Qwen weights if they are not included in your package. **Verify / Repair** checks existing files and downloads only missing or damaged ones. The model requires approximately 4.38 GiB. Japanese lyrics and notes-only extraction work without it. See [Interface languages](docs/InterfaceLanguages.en.md) for the separate display-language setting.

Download options include Automatic, Direct, Prefer acceleration and named providers. GitHub files support GitProxy, GH-Proxy and ghfast; Hugging Face supports HF-Mirror; pinned runtime packages support TUNA, Aliyun and SJTU where available. See [Download sources](docs/DownloadSources.en.md). Availability and speed depend on the network and selected service.

## Program updates

Open **设置与更新** (Settings/updates) to check versions, read release notes and choose the default download source. Automatic checks, downloads and installation run only at startup by default. Verified startup updates show a ten-second restart countdown, then install and reopen the application. Choose **暂缓安装** (Postpone) to continue using the current version. If processing or another operation has started, installation is postponed to the next launch. There are no periodic update checks during use. Manually downloaded updates require **重启并安装更新** (Restart/install).

Program updates reuse installed models and preserve settings, tasks and sign-in sessions. Failed file replacement restores the previous program files. Follow the release instructions for runtime changes. Older installations can use the [compatible upgrade path](docs/UpdateProtocol.en.md) and `VWA-Updater.exe` when directed.

The release catalog’s `portable_downloads.full` and `portable_downloads.jpn` entries identify the canonical VWA packages. Use the package intended for your installation; update archives are not complete installations.

## Upload preparation

Open **发布** (Publishing), after the optional **合成视频** (Video assembly) page. Each page shows one step:

1. **Finished video/source:** select the finished video and its source task, or fetch source information from a BV identifier.
2. **Cover:** preview the original cover at a large size, choose a custom image, or capture a video frame. Local music tasks require a custom image unless BV source information is added.
3. **Title/description:** review both platforms side by side, apply the singer/title template and check credits and character limits.
4. **Account/upload:** select **生成发布资料** (Prepare assets), then sign in and select **上传所选平台** (Upload selected platforms). The main action stays visible at the bottom while the page scrolls.

Use Previous/Next or the numbered steps to move between pages. Next checks the required inputs before advancing. Each platform has visible sign-in, resume and close controls; unavailable actions are disabled. Close the sign-in window after logging in. The assistant leaves creator pages open for review and manual publishing.

Original covers come from video metadata. Frame capture and custom images are separate choices. Cover conversion preserves the complete image with padding for 4:3 and 16:9 when needed.

Regenerate publication files after changing a finished video, cover or draft. **继续填写当前上传** (Continue current upload) updates an open task with the same verified video. Closed browsers require a new upload. Review categories, tags, credits and remaining required fields on the creator page before publishing.

![Publishing cover preview](docs/images/publishing-cover.png)

## Requirements and troubleshooting

Bilibili metadata, original covers and media downloads use direct connections and do not inherit system or environment proxy settings. Component downloads and update checks connect directly to the selected official or accelerated source. Runtime installation and the assistant's dedicated sign-in/upload browser also use direct connections. If retrieval fails, check access to the source, then retry or choose another download source.

- Windows 10/11 x64; 16 GB RAM is recommended.
- Reserve approximately 25 GB for downloading, joining and extracting the portable package. Optional components and media need additional space.
- Keep `dependencies`, `models`, `assets` and `components` with the executable. Transfer the complete folder to another computer.
- Startup errors are recorded in `launcher.log`. Diagnostic controls are in `tools/`.
- Platform sign-in is required on each new computer.

| Diagnostic tool | Purpose |
| --- | --- |
| `tools/Start-Diagnostics.bat` | Start with visible diagnostic output; install a missing runtime |
| `tools/Check-Dependencies.bat` | Verify bundled runtime files |
| `tools/Use-CPU.bat`, `tools/Use-GPU.bat` | Select CPU/DirectML for the base pipeline |

## Development

Application code is in `src/`; configuration templates and build inventories are in `packaging/`. Tests, build tools and documentation have their own folders. Models and large runtime files are distributed separately.

For a source checkout, `tools/Start-Diagnostics.bat` prepares the development layout and installs a missing runtime. Edit files in `src/`, then run the preparation command below before testing or building. It copies source files to the runtime layout without changing user settings or project data.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Prepare-Source.ps1
& '.\dependencies\vocal2midi\python\python.exe' -B .\tools\build_windows_launcher.py
& '.\dependencies\vocal2midi\python\python.exe' -B .\tests\run_tests.py
& '.\dependencies\vocal2midi\python\python.exe' -B .\pipeline.py --audio-file 'D:\Music\song.wav' --zh-lyric-mode pinyin
```

User data under `cache/`, `jobs/` and `publish-packages/` is excluded from clean packages and Git. See [Contributing](CONTRIBUTING.md), [Changelog](CHANGELOG.md) and the [architecture](docs/Architecture.en.md). Release packaging requires passing automated checks and confirmed user testing.

## License

The assistant’s original source is licensed under [MIT](LICENSE). Bundled libraries and model weights retain their own licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md).

Large component and update downloads use up to 8 concurrent connections where HTTP Range is supported, with resumable segments and SHA-256 verification. Unsupported servers fall back to one connection. GitHub uploads use the official endpoint directly.

## Related projects and acknowledgements


This assistant integrates existing tools and model pipelines. Thanks to their authors and contributors.

| Project | Use in this assistant |
| --- | --- |
| [LibreSVIP](https://github.com/SoulMelody/LibreSVIP) | Core project conversion to USTX, VSQX and SVP; MIT |
| [163MusicLyrics](https://github.com/jitwxs/163MusicLyrics) | Reference for public lyric lookup behavior; its desktop program is not bundled |
| [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi) | Vocal transcription and lyric-aligned MIDI through its bundled application layer; Apache-2.0 repository |
| [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) and [UVR model repository](https://github.com/TRvlvr/model_repo) | MDX inference reference, separation weights and parameter metadata; the UVR desktop app is not required |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Bilibili media downloading |
| [FFmpeg](https://ffmpeg.org/) / [Jellyfin FFmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) | Audio decoding, media inspection and cover-frame extraction |
| [Playwright for Python](https://github.com/microsoft/playwright-python) | Browser sign-in and creator-page automation |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | CPU / DirectML model execution |
| [PyQt](https://www.riverbankcomputing.com/software/pyqt/) | Desktop interface |
| [Pymss](https://github.com/pymss-project/pymss) | Optional BS-RoFormer inference API and model implementation |
| [Pymss Studio](https://github.com/pymss-project/pymss-studio) | Workspace layout and model-management workflow reference |
| [becruily / BS-RoFormer karaoke](https://huggingface.co/becruily/bs-roformer-karaoke) | Original optional checkpoint and configuration |
| [PyTorch](https://pytorch.org/) | Optional CPU / CUDA inference runtime |
| [PyWORLD](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder) / [WORLD](https://github.com/mmorise/World) | Optional harmonic and aperiodic analysis for reference-expression curves; MIT / BSD-3-Clause |
| [NumPy](https://numpy.org/), [SciPy](https://scipy.org/), [librosa](https://librosa.org/), [SoundFile](https://github.com/bastibe/python-soundfile), [Mido](https://github.com/mido/mido), [Pillow](https://python-pillow.org/) | Audio, numeric, MIDI and image processing |

Vocal2Midi's pipeline also builds on [GAME](https://github.com/openvpi/GAME), [HubertFA](https://github.com/wolfgitpr/HubertFA), [LyricFA](https://github.com/wolfgitpr/LyricFA), and [llama.cpp](https://github.com/ggml-org/llama.cpp), alongside its ASR and pitch models. See its preserved [acknowledgements](dependencies/vocal2midi/ACKNOWLEDGEMENTS.md) and the [third-party notices](THIRD_PARTY_NOTICES.md) for attribution and component-specific terms. These projects and models retain their own licenses; this list does not grant a common license to the package.
