<p align="center"><img src="assets/app.svg" width="80" alt="VWA icon"></p>

# VWA — Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md)

A Windows assistant for preparing vocal projects, tuning SVP files, assembling videos and preparing uploads.

## Start

1. Download `VWA-Windows-x64.zip` (full) or `VWA-Windows-x64-JPN.zip` (JPN) from [Releases](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases), then extract it to a writable location. The extracted top-level folder is `VWA`; start **`VWA.exe`** from that folder and keep the accompanying folders together.
2. In **Preparation**, add a Bilibili BV/link or local song, then review separation, lyrics, notes and output choices. Select **Start preparation**.
3. Find the audio, MIDI and projects in **Tasks and results**. Continue interrupted tasks there; reprocessing with current settings creates a new task.
4. In **Tuning**, open a task project or import an SVP. Select tracks and one primary parameter source, then save a separate project. Style, pitch and curve density have separate controls. Drag strength in 1% steps or enter a precise value.
5. Render the singing voice in your voice editor. Use **Assemble video** if needed, or send an existing finished video directly to **Publishing**.

Review audio, lyrics and notes before use. Final publication is confirmed by you on the platform website.

## Choose a package

| Package | File | Use |
| --- | --- | --- |
| Full | `VWA-Windows-x64.zip` | Chinese/Japanese recognition, note extraction and all four workflows |
| JPN | `VWA-Windows-x64-JPN.zip` | The same workflows without the Chinese Qwen weights; Japanese recognition and note extraction remain available |
| Update | As listed in the release | Upgrade a compatible existing installation; not a complete installation |

With JPN, select Japanese recognition in Preparation. To use Chinese lyrics, open **Models → Chinese lyric recognition → Download Chinese model**. Existing files can be verified and repaired.

If a download is split, keep all parts with the matching `portable-metadata-full.json` or `portable-metadata-jpn.json` and use the merge helper supplied with that package. GitHub Source code archives do not include the runtime.

## Settings and outputs

- Choose Chinese, Japanese or English under **Settings and updates → Appearance → Interface language**. Changes apply on the next launch. Lyric recognition is set separately in Preparation.
- Appearance also offers light/dark themes, Moonlight glass/solid panels and interface scaling.
- Choose automatic, direct or supported accelerated downloads in Settings or Models. See [download sources](docs/DownloadSources.en.md) and [interface languages](docs/InterfaceLanguages.en.md).
- Preparation supports lead/backing tracks, reference lyrics, Chinese characters/pinyin, optional pitch curves and manual BPM. Select MIDI, editor projects and audio separately; set the save folder in Output.
- Edit timed lyrics before exporting SRT/WebVTT. Text without timing is not automatically divided into subtitles.
- Manage caches in Settings. Task outputs, model weights and sign-in sessions are protected from ordinary cache cleanup.

## Video and publishing

Video assembly is optional. Choose rendered audio and a video, align them if needed, then create the finished video.

In Publishing, choose the finished video and source task, review the cover and credits, then prepare the selected platform. Original Bilibili covers come from metadata; custom images and video frames are separate choices. Cover fitting keeps the full image with padding for 4:3/16:9 when needed.

Review the creator page and confirm publication manually. Keep the finished video in place until upload; regenerate publication files after moving or changing it.

## Updates and diagnostics

Use Settings and updates for a compatible upgrade route. Automatic updates run at startup and install after a restart. Settings, tasks, sign-in sessions and installed models are retained when the offered update supports them. Back up projects you are editing before upgrading.

Older installations can follow the release notes and use **`VWA-Updater.exe`** when it is offered as the compatibility tool. It is not a complete portable installation.

Keep `dependencies`, `models`, `assets`, `components` and `vendor` with the executable. Startup errors are recorded in `launcher.log`. `tools/Start-Diagnostics.bat` shows startup diagnostics; `tools/Check-Dependencies.bat` checks runtime files. In JPN, add the Chinese model from Models when needed.

Project code: [MIT license](LICENSE). Third-party terms: [notices](THIRD_PARTY_NOTICES.md). [Project repository](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant).
