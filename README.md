<p align="center"><img src="assets/app.svg" width="80" alt="VWA icon"></p>

# VWA · Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

VWA is a Windows desktop assistant for preparing singing projects, tuning by lyric, mixing vocals, assembling videos, and preparing uploads.

**User community: QQ group 490619616.** Join for help and discussion.

[Feedback and suggestions](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose) · [Contribution guide](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/blob/main/CONTRIBUTING.md)

**[Download VWA 2.0.0 for Windows x64](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/tag/v2.0.0)** · [User guide](docs/UserGuide.en.md)

VWA 2.0.0 introduces a Qt Quick workbench with single-track and multitrack tuning, continuous mix audition, and live analysis. The first launch offers a skippable tour. When an update is available, VWA shows the changes and lets you decide when to install it.

## Features

| Workflow | What it does |
| --- | --- |
| Preparation | Import local audio or a Bilibili BV/link, separate lead vocals, backing vocals and accompaniment, transcribe Chinese or Japanese lyrics and notes, and export MIDI, USTX, VSQX or SVP. |
| Tuning | Open a task project or local SVP, USTX, or VPR, apply experimental styles and pitch gestures to vocal tracks or lyric regions, and save a separate project in the same format. |
| Simple mixing | Balance up to three rendered vocal WAVs and one accompaniment with track level, pan, mute/solo, vocal timing and reverb sends, shared vocal-bus EQ/compression/reverb, and editable A/B previews. |
| Video assembly | Combine a video with your Mixdown, with automatic alignment, manual offset and optional loudness adjustment. |
| Publishing | Prepare covers, titles, descriptions and credits. Bilibili supports assisted upload; Xiaohongshu provides a local handoff for manual posting and AI disclosure. |
| Agent/MCP (optional) | Connect a compatible host to 12 typed top-level VWA tools for preparation, evidence review, decisions, export, recovery and delivery. Setup is separate from the desktop application. |

Tuning offers preservation of existing expression, reference-vocal mapping, measured voicebank calibration, transfer of existing parameters, time-region style editing, style autosave and an independent Chinese-opera technique. Reference mapping and the Gentle, Sad and Bright & Cheerful styles are experimental drafts; synthesize and audition them with the target voicebank. Calibration requires a matching score and voicebank setup, with trial audio exported from SV by the user. Voice synthesis takes place in your singing editor.

[Simple mixing](docs/SimpleMixingComponent.en.md) uses one native continuous PCM preview with a shared playback session across pages and minimization. Track meters, EQ spectrum, gain reduction estimates and reverb readings use actual preview data. Device failures pause playback and require an explicit retry. Check the complete song on the intended voicebank and audio devices. Source files are preserved.

VWA also provides task history and recovery, saved presets, optional model installation, and SRT/WebVTT subtitle export. The interface supports Simplified Chinese, Japanese and English, with light/dark themes and interface scaling. The MCP technical preview is optional, installed separately, and does not add a general command executor.

## Download and installation

Choose the full or JPN package under **Assets** on the [2.0.0 release page](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/tag/v2.0.0).

| Package | Use and contents |
| --- | --- |
| Full | Clean installation for all users, including new users and users on any older version. Includes the complete model set and base runtime. |
| JPN | The same workflows with only the Chinese Qwen weight files omitted. Japanese recognition and note extraction remain available; Chinese models can be installed later. |
| Update | Available in the app for released **1.0.0 and 1.1.0** installations only. |

Both full variants support all three interface languages. Interface language and lyric-recognition language are set separately. GitHub's **Source code** archives and update ZIPs are not complete installations.

**Full package (about 5.41 GB):** Put `VWA-Windows-x64.zip.001`, `.002`, `.003`, `portable-metadata-full.json`, and `Merge-PortableParts.ps1` in one folder. Open PowerShell there and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Merge-PortableParts.ps1
```

The script verifies all three parts and creates `VWA-Windows-x64.zip`. For **JPN (about 1.74 GB)**, download and extract `VWA-Windows-x64-JPN.zip` directly. Use a short, writable path such as `D:\Apps`, then run **`VWA.exe`** inside the extracted `VWA` folder. Keep the whole folder together when moving it.

Requirements: Windows 10/11 x64. The portable package includes its runtime. Check the published release notes for the final download, joining and extraction space; leave additional room for optional components and media. A compatible singing editor and voicebank are required for voice synthesis.

## Basic use

1. In **Preparation**, select the source, lyric language, processing options and output folder. Review the resulting audio, notes and lyrics.
2. In **Tuning**, open a task project or import a Synthesizer V Studio 1/2 SVP, OpenUtau USTX, or VOCALOID 5/6 VPR. Find a lyric, select the tracks and range, and save a new project for rendering and auditioning in its editor.
3. Render the vocal stems. Open **Simple mixing** to balance up to three vocal WAVs and one accompaniment, audition the result and export a new WAV or register it as a task Mixdown.
4. Select the original video and Mixdown in **Assemble video**. An existing finished video can go directly to **Publishing**.
5. Check the cover, platform drafts and credits before preparing the upload. Review the creator page and publish manually.

Tuning can open supported native Synthesizer V Studio 2 (SV2) projects directly. For independently placed note groups, choose **Keep original expression**, apply a style, and save a separate candidate. Shared groups, trims that hide notes, and unverified project versions are read-only. Open the candidate in SV2 to audition it.

For OpenUtau USTX, **Keep original expression** supports additive dynamics (DYN) and pitch deviation (PITD) while preserving note pitch shapes, vibrato, phonemes, and other expressions. Save a USTX candidate and open it in OpenUtau to audition it. USTX 0.5 can be previewed; resave it in OpenUtau before editing.

For VOCALOID 5/6 VPR, **Keep original expression** supports additive dynamics (DYN) and pitch bend (PIT) while preserving PBS, voices, note pitch data, and embedded audio. Overlapping notes are left alone, and PIT is skipped on notes with drawn pitch. Save a VPR candidate and open it in VOCALOID to audition it.

Use **Tasks and results** to find, continue, or retry tasks. Install optional models from **Models and components**. In **Settings and support**, change the interface language, revisit the tour, or manage the [local preset library](docs/PresetLibrary.en.md). Imports append presets by default.

## Upgrading

**Released 1.0.0 or 1.1.0:** Check for updates in the old app under **Settings and updates**, then follow the restart prompt. Both full and JPN retain settings, tasks, presets, sign-in sessions, installed models, and optional components. JPN does not automatically add omitted Chinese weights.

**All other versions:** Extract a full or JPN package into a new folder. Back up editor projects and apply an offered update through the app.

## Documentation and support

[User guide](docs/UserGuide.en.md) · [Tuning guide](docs/TuningWorkflow.en.md) · [Portable guide](docs/Portable.en.md) · [MCP tool reference (Chinese)](docs/AgentMCPStandard.zh-CN.md) · [Changelog](CHANGELOG.md) · [Report an issue](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose)

## Acknowledgements

Thanks to the authors and contributors of the following projects.

| Project | Use in VWA |
| --- | --- |
| [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi) | Note transcription and lyric alignment. |
| [LibreSVIP](https://github.com/SoulMelody/LibreSVIP) | USTX, VSQX and SVP conversion. |
| [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) / [UVR models](https://github.com/TRvlvr/model_repo) | Vocal separation, model weights and metadata. |
| [Pymss](https://github.com/pymss-project/pymss) / [becruily BS-RoFormer karaoke](https://huggingface.co/becruily/bs-roformer-karaoke) | Optional separation engine and model. |
| [Pymss Studio](https://github.com/pymss-project/pymss-studio) | Interface and model-management design reference. |
| [PyWORLD](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder) / [WORLD](https://github.com/mmorise/World) | Reference-vocal acoustic analysis. |
| [163MusicLyrics](https://github.com/jitwxs/163MusicLyrics) | Lyric-lookup behavior reference. |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Media downloading. |
| [FFmpeg](https://ffmpeg.org/) / [Jellyfin FFmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) | Audio/video processing. |
| [Playwright for Python](https://github.com/microsoft/playwright-python) | Browser sign-in and upload assistance. |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) / [PyTorch](https://pytorch.org/) | Model execution. |
| [Qt Quick and PySide6](https://doc.qt.io/qtforpython-6.10/) | Desktop interface. |
| [NumPy](https://numpy.org/), [SciPy](https://scipy.org/), [librosa](https://librosa.org/), [SoundFile](https://github.com/bastibe/python-soundfile), [Mido](https://github.com/mido/mido), [Pillow](https://python-pillow.org/) | Numeric, audio, MIDI and image processing. |

The integrated Vocal2Midi pipeline also uses [GAME](https://github.com/openvpi/GAME), [HubertFA](https://github.com/wolfgitpr/HubertFA), [LyricFA](https://github.com/wolfgitpr/LyricFA), [RomajiASR](https://github.com/Xiantaidu/RomajiASR), [llama.cpp](https://github.com/ggml-org/llama.cpp) and its associated speech and pitch models. See the preserved [upstream acknowledgements](dependencies/vocal2midi2/ACKNOWLEDGEMENTS.md) for further credits.

## License

VWA's original code is licensed under [MIT](LICENSE). Third-party code, runtimes and model weights retain their own licenses and notices. See [Third-party notices](THIRD_PARTY_NOTICES.md).

See [2.0.0 runtime notices and corresponding source](docs/ThirdPartyRuntime.md).
