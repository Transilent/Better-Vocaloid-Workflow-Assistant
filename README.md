<p align="center"><img src="assets/app.svg" width="80" alt="VWA icon"></p>

# VWA · Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

VWA is a Windows desktop assistant for preparing singing-synthesis projects, tuning SVP projects, assembling videos and preparing uploads.

## Features

| Workflow | What it does |
| --- | --- |
| Preparation | Import local audio or a Bilibili BV/link, separate lead vocals, backing vocals and accompaniment, transcribe Chinese or Japanese lyrics and notes, and export MIDI, USTX, VSQX or SVP. |
| Tuning | Open a task project or local SVP, select vocal tracks and a parameter source, optionally add experimental styles, style regions and local pitch gestures, and save a separate project. |
| Simple mixing | Balance up to three rendered vocal WAVs and one accompaniment with track level, pan, mute/solo, vocal timing and reverb sends, shared vocal-bus EQ/compression/reverb, and editable A/B previews. |
| Video assembly | Combine a video with your Mixdown, with automatic alignment, manual offset and optional loudness adjustment. |
| Publishing | Prepare covers, titles, descriptions and credits, and assist with Bilibili and Xiaohongshu uploads. Confirm publication on the platform website. |
| Agent/MCP (optional) | Connect a compatible host to 12 typed top-level VWA tools for preparation, evidence review, decisions, export, recovery and delivery. Setup is separate from the desktop application. |

Tuning offers preservation of existing expression, reference-vocal mapping, measured voicebank calibration, transfer of existing parameters, time-region style editing, style autosave and an independent Chinese-opera technique. Reference mapping and the Gentle, Sad and Bright & Cheerful styles are experimental drafts; synthesize and audition them with the target voicebank. Calibration requires a matching score and voicebank setup, with trial audio exported from SV by the user. Voice synthesis takes place in your singing editor.

[Simple mixing](docs/SimpleMixingComponent.en.md) works on up to three rendered vocal WAVs and one accompaniment. Its A/B preview can continue across short rendered segments and refresh after edits while preserving playback when possible. When an audio output device fails, VWA retries once with its compatible built-in player and, if needed, offers the current segment in the system player. The handoff and buffered segments may differ briefly from a full export; check the complete song on your own voicebank and audio devices. Source files are not overwritten.

VWA also provides task history and recovery, saved presets, optional model installation, and SRT/WebVTT subtitle export. The interface supports Simplified Chinese, Japanese and English, with light/dark themes and interface scaling. The MCP technical preview is optional, installed separately, and does not add a general command executor.

## Download and installation

Download the full or JPN package from the [1.1.0 release](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/tag/v1.1.0).

| Package | Use and contents |
| --- | --- |
| Full | Clean installation for all users, including new users and users on any older version. Includes the complete model set and base runtime. |
| JPN | The same workflows with only the Chinese Qwen weight files omitted. Japanese recognition and note extraction remain available; Chinese models can be installed later. |
| Update | Only an installed version explicitly listed as compatible in the 1.1.0 release may use an offered update. An update archive is not a complete installation. |

Both full variants support all three interface languages. Interface language and lyric-recognition language are set separately. GitHub's **Source code** archives and update ZIPs are not complete installations.

**Full package:** if the release splits the full asset, download every part for that asset together with its matching metadata and merge helper. Open PowerShell in that folder and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Merge-PortableParts.ps1
```

The script verifies the parts and creates `VWA-Windows-x64.zip`. Extract that ZIP completely. If JPN is supplied as one ZIP, extract it directly. Open the extracted `VWA` folder and run **`VWA.exe`**. Keep the complete folder together when moving the application.

Requirements: Windows 10/11 x64. The portable package includes its runtime. Check the published release notes for the final download, joining and extraction space; leave additional room for optional components and media. A compatible singing editor and voicebank are required for voice synthesis.

## Basic use

1. In **Preparation**, select the source, lyric language, processing options and output folder. Review the resulting audio, notes and lyrics.
2. In **Tuning**, choose a task project or import an SVP. Select the tracks, parameter source and optional experimental style, then generate a separate project for auditioning in SV.
3. Render the vocal stems. Open **Simple mixing** to balance up to three vocal WAVs and one accompaniment, audition the result and export a new WAV or register it as a task Mixdown.
4. Select the original video and Mixdown in **Assemble video**. An existing finished video can go directly to **Publishing**.
5. Check the cover, platform drafts and credits before preparing the upload. Review the creator page and publish manually.

Use **Tasks and results** to find, continue or retry tasks. Install optional models under **Models and components**. Change the interface language under **Settings and updates → Appearance → Interface language**, then restart VWA.

## Upgrading

| Installed version | 1.1.0 route |
| --- | --- |
| 1.0.0 | Check for and install 1.1.0 in **Settings and updates**. The 1.0.0 JPN variant updates to 1.1.0 JPN. |
| 0.4.0 / 0.4.1 | Direct updates to 1.1.0 are not currently provided. Use the full package in a new folder. |
| 0.3.0 / 0.3.1 and other versions | Use the full package in a new folder. Older `VWA-Updater.exe` downloads are not a 1.1.0 upgrade route. |

The 1.0.0 JPN update targets 1.1.0 JPN: it does not force a download of omitted Chinese weights, and models installed later are preserved.

Do not unpack an update archive over an installation; use only the update offered by the application or release notes. A listed compatible update preserves settings, tasks, sign-in sessions, installed models and optional components. Back up editor projects before upgrading.

## Documentation and support

[User guide](docs/UserGuide.en.md) · [Tuning guide](docs/TuningWorkflow.en.md) · [Portable guide](docs/Portable.en.md) · [MCP tool reference (Chinese)](docs/AgentMCPStandard.zh-CN.md) · [Changelog](CHANGELOG.md) · [Report an issue](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues)

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
| [PyQt](https://www.riverbankcomputing.com/software/pyqt/) | Desktop interface. |
| [NumPy](https://numpy.org/), [SciPy](https://scipy.org/), [librosa](https://librosa.org/), [SoundFile](https://github.com/bastibe/python-soundfile), [Mido](https://github.com/mido/mido), [Pillow](https://python-pillow.org/) | Numeric, audio, MIDI and image processing. |

The integrated Vocal2Midi pipeline also uses [GAME](https://github.com/openvpi/GAME), [HubertFA](https://github.com/wolfgitpr/HubertFA), [LyricFA](https://github.com/wolfgitpr/LyricFA), [RomajiASR](https://github.com/Xiantaidu/RomajiASR), [llama.cpp](https://github.com/ggml-org/llama.cpp) and its associated speech and pitch models. See the preserved [upstream acknowledgements](dependencies/vocal2midi2/ACKNOWLEDGEMENTS.md) for further credits.

## License

VWA's original code is licensed under [MIT](LICENSE). Third-party code, runtimes and model weights retain their own licenses and notices. See [Third-party notices](THIRD_PARTY_NOTICES.md).
