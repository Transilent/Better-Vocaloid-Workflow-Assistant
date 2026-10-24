<p align="center"><img src="assets/app.svg" width="80" alt="VWA icon"></p>

# VWA · Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

VWA is a Windows desktop assistant for preparing singing-synthesis projects, tuning SVP projects, assembling videos and preparing uploads.

## Features

| Workflow | What it does |
| --- | --- |
| Preparation | Import local audio or a Bilibili BV/link, separate lead vocals, backing vocals and accompaniment, transcribe Chinese or Japanese lyrics and notes, and export MIDI, USTX, VSQX or SVP. |
| Tuning | Open a task project or local SVP, select vocal tracks and a parameter source, optionally add a singing style and local pitch gestures, and save a separate project. |
| Video assembly | Combine a video with your Mixdown, with automatic alignment, manual offset and optional loudness adjustment. |
| Publishing | Prepare covers, titles, descriptions and credits, and assist with Bilibili and Xiaohongshu uploads. Confirm publication on the platform website. |

Tuning offers preservation of existing expression, reference-vocal mapping, measured voicebank calibration and transfer of existing parameters. Reference mapping and style presets produce editable drafts; audition them with the target voicebank. Calibration requires a matching score and voicebank setup, with trial audio exported from SV by the user. Voice synthesis and mixing take place in your singing editor and audio software.

VWA also provides task history and recovery, saved presets, optional model installation, and SRT/WebVTT subtitle export. The interface supports Simplified Chinese, Japanese and English, with light/dark themes and interface scaling.

## Download and installation

Download version **1.0.0** from [Releases](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/tag/v1.0.0).

| Package | Download size | Contents |
| --- | ---: | --- |
| Full | About 4.91 GiB, in three parts | Chinese and Japanese recognition models and the base runtime. |
| [JPN](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/download/v1.0.0/VWA-Windows-x64-JPN.zip) | About 1.53 GiB | Omits only the three Chinese Qwen weight files. Japanese recognition and note extraction remain available; Chinese models can be installed later. |

Both packages support all three interface languages. Interface language and lyric-recognition language are set separately. GitHub's **Source code** archives and update ZIPs are not complete installations.

**Full package:** download these five files into the same folder:

- `VWA-Windows-x64.zip.001`, `.002` and `.003`
- `portable-metadata-full.json`
- `Merge-PortableParts.ps1`

Open PowerShell in that folder and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Merge-PortableParts.ps1
```

The script verifies the parts and creates `VWA-Windows-x64.zip`. Extract that ZIP completely. For JPN, extract the downloaded ZIP directly. Open the extracted `VWA` folder and run **`VWA.exe`**. Keep the complete folder together when moving the application.

Requirements: Windows 10/11 x64. The portable package includes its runtime. For the full package, allow about 25 GB for downloading, joining and extraction, plus space for optional components and media. A compatible singing editor and voicebank are required for voice synthesis.

## Basic use

1. In **Preparation**, select the source, lyric language, processing options and output folder. Review the resulting audio, notes and lyrics.
2. In **Tuning**, choose a task project or import an SVP. Select the tracks, parameter source and optional style, then generate a separate project for auditioning in SV.
3. After rendering and mixing the vocals, select the original video and Mixdown in **Assemble video**. An existing finished video can go directly to **Publishing**.
4. Check the cover, platform drafts and credits before preparing the upload. Review the creator page and publish manually.

Use **Tasks and results** to find, continue or retry tasks. Install optional models under **Models and components**. Change the interface language under **Settings and updates → Appearance → Interface language**, then restart VWA.

## Upgrading

| Installed version | Upgrade method |
| --- | --- |
| 0.4.0 / 0.4.1 | Follow the upgrade offered in **Settings and updates**. |
| 0.3.0 / 0.3.1 | Use [VWA-Updater.exe](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/download/v1.0.0/VWA-Updater.exe) and follow its prompts. |

Supported upgrades preserve settings, tasks, sign-in sessions, installed models and optional components. Back up editor projects before upgrading. Release files named `BVWA` remain available for older clients.

## Documentation and support

[User guide](docs/UserGuide.en.md) · [Tuning guide](docs/TuningWorkflow.en.md) · [Changelog](CHANGELOG.md) · [Report an issue](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues)

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
