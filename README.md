# Better Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md)

A Windows x64 assistant for preparing singing-synthesis projects. Start with a Bilibili video or local music, separate vocals and accompaniment, and export lyric-aligned MIDI. After manual tuning and video editing, prepare covers, credits, and creator uploads.

## Quick start

1. Download the **portable package** from [Releases](../../releases). The automatically generated **Source code** archive does not include the runtime or models.
2. Extract the complete `BVWA` folder to a writable, short path, such as `D:\BVWA`.
3. Double-click **`Start-Assistant.bat`**. This is the only application launcher.
4. Choose **Bilibili BV/link** or **local music**, set the separation mode and optional lyrics, then start processing.
5. Import MIDI and accompaniment into a tuning application. Complete tuning and align the mixdown with the video manually.
6. Select the finished video in the publishing tab, edit titles and credits, and sign in. Review the prepared upload and publish manually.

The interface currently uses Chinese labels. The [English guide](docs/UserGuide.en.md) explains them; a [Chinese guide](docs/UserGuide.zh-CN.md) is also available.

## Features

| Input or mode | Behavior | Main output |
| --- | --- | --- |
| Bilibili BV/link | Fetch source metadata, video, audio, and original cover | Source assets and credits draft |
| Local WAV / MP3 / FLAC / M4A | Save a snapshot and decode locally; no BV required | Normalized source audio |
| Automatic two-stage separation | Separate vocals/accompaniment, then lead/backing vocals | Vocal parts, accompaniment, two-part MIDI |
| External stems | Import full-length lead, backing, and accompaniment WAVs | Two-part MIDI |
| Combined-vocal mode | Transcribe the combined vocal stem | Single-part MIDI |
| Upload preparation | Fill source credits, titles, and covers | Bilibili / Xiaohongshu upload awaiting review |

Models run locally through command-line interfaces. Browser automation uses Playwright DOM controls. Separation can leave leakage; transcription and lyrics need listening and correction. Tuning, finished mixdown/video alignment, and final publishing are manual.

The original cover is the image shown on the source video's Bilibili listing, fetched from metadata. A video frame is an explicit alternative. Bilibili 4:3 covers preserve the whole image with padding; 16:9 covers preserve the source ratio.

## Portable package

Includes Python, inference code and models, FFmpeg, browser runtime, and automation dependencies. Targets **Windows 10/11 x64**. Recommended: at least 16 GB RAM and 12 GB free space. DirectML/Vulkan acceleration depends on the hardware; CPU inference is available and slower.

Release files are split to satisfy GitHub's attachment limits. Download all `.zip.00x` parts and `release-assets.json` to one folder. Open `.zip.001` with 7-Zip, or use `Merge-PortableParts.ps1` to verify and join the parts. Extract the resulting ZIP before running the application.

| Batch file | Purpose |
| --- | --- |
| `Start-Assistant.bat` | Launch the application, the only startup entry |
| `Check-Dependencies.bat` | Verify fixed runtime files using SHA-256 |
| `Use-CPU.bat` | Select CPU inference; restart afterward |
| `Use-GPU.bat` | Select DirectML inference; restart afterward |

There is no additional `.cmd` launcher. Each new computer requires fresh Bilibili and Xiaohongshu sign-in.

## Source and development

Git stores assistant source, frozen integration source, tests, configuration, packaging tools, and the dependency inventory. Large binaries and models are stored in private Release assets. For development, extract the portable package, then overlay the repository source.

```powershell
& '.\dependencies\vocal2midi\python\python.exe' -B .\launch.py --check
& '.\dependencies\vocal2midi\python\python.exe' -B .\tests\run_tests.py
& '.\dependencies\vocal2midi\python\python.exe' -B .\pipeline.py --audio-file 'D:\Music\song.mp3' --voice-mode dual
```

Account profiles, cookies, tokens, personal media, job history, and publication drafts are excluded from Git and release packages. Runtime data lives in `cache/`, `jobs/`, and `publish-packages/`.

See the [guide](docs/UserGuide.en.md), [architecture](docs/Architecture.en.md), [verification](docs/Verification.en.md), and [third-party notices](THIRD_PARTY_NOTICES.md).

## Related projects and acknowledgements

This assistant integrates existing tools and model pipelines. Thanks to their authors and contributors.

| Project | Use in this assistant |
| --- | --- |
| [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi) | Vocal transcription and lyric-aligned MIDI through its bundled application layer; Apache-2.0 repository |
| [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) and [UVR model repository](https://github.com/TRvlvr/model_repo) | MDX inference reference, separation weights and parameter metadata; the UVR desktop app is not required |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Bilibili media downloading |
| [FFmpeg](https://ffmpeg.org/) / [Jellyfin FFmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) | Audio decoding, media inspection and cover-frame extraction |
| [Playwright for Python](https://github.com/microsoft/playwright-python) | Browser sign-in and creator-page automation |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | CPU / DirectML model execution |
| [PyQt](https://www.riverbankcomputing.com/software/pyqt/) | Desktop interface |
| [NumPy](https://numpy.org/), [SciPy](https://scipy.org/), [librosa](https://librosa.org/), [SoundFile](https://github.com/bastibe/python-soundfile), [Mido](https://github.com/mido/mido), [Pillow](https://python-pillow.org/) | Audio, numeric, MIDI and image processing |

Vocal2Midi's pipeline also builds on [GAME](https://github.com/openvpi/GAME), [HubertFA](https://github.com/wolfgitpr/HubertFA), [LyricFA](https://github.com/wolfgitpr/LyricFA), and [llama.cpp](https://github.com/ggml-org/llama.cpp), alongside its ASR and pitch models. See its preserved [acknowledgements](dependencies/vocal2midi/ACKNOWLEDGEMENTS.md) and the [third-party notices](THIRD_PARTY_NOTICES.md) for attribution and component-specific terms. These projects and models retain their own licenses; this list does not grant a common license to the package.
