<p align="center"><img src="assets/app.svg" width="80" alt="VWA icon"></p>

# VWA — Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md)

A Windows assistant for preparing vocal projects, tuning SVP files, assembling videos and preparing uploads.

## Start

1. When a release explicitly identifies version 1.1.0, download `VWA-Windows-x64.zip` (full) or `VWA-Windows-x64-JPN.zip` (JPN) from [Releases](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases). Extract a full package to a new, writable folder. The extracted top-level folder is `VWA`; start **`VWA.exe`** from that folder and keep the accompanying folders together.
2. In **Preparation**, add a Bilibili BV/link or local song, then review separation, lyrics, notes and output choices. Select **Start preparation**.
3. Find the audio, MIDI and projects in **Tasks and results**. Continue interrupted tasks there; reprocessing with current settings creates a new task.
4. In **Tuning**, open a task project or import an SVP. Select tracks and one primary parameter source, then save a separate project. Experimental style, pitch and curve density have separate controls. Drag strength in 1% steps or enter a precise value; synthesize and audition the result in your voice editor.
5. Render the vocal stems, then use **Simple mixing** to balance up to three vocal WAVs and one accompaniment. Export a new WAV or explicitly register it as the task Mixdown.
6. Use **Assemble video** if needed, or send an existing finished video directly to **Publishing**.

Review audio, lyrics and notes before use. Final publication is confirmed by you on the platform website.

## Choose a package

| Package | File | Use |
| --- | --- | --- |
| Full | `VWA-Windows-x64.zip` | Clean installation for all users, including new users and users on every older version; includes the complete model set, runtime, preparation, tuning, simple mixing, video assembly and publishing. |
| JPN | `VWA-Windows-x64-JPN.zip` | The same workflows without the Chinese Qwen weights; Japanese recognition and note extraction remain available. |
| Update | As listed in the release | Only for an installed version explicitly supported by that 1.1.0 release; not a complete installation. |

The 1.1.0 release gate requires a verified update route from 1.0.0. Updating 1.0.0 JPN to 1.1.0 JPN does not force a download of omitted Chinese weights and preserves models installed later. Treat 0.4.0 and 0.4.1 as supported update sources only if the published release explicitly lists that exact version after compatibility and rollback checks. Every other version, and either 0.4 version without an explicit listing, should use the full package in a new folder.

With JPN, select Japanese recognition in Preparation. To use Chinese lyrics, open **Models → Chinese lyric recognition → Download Chinese model**. Existing files can be verified and repaired.

If a download is split, keep all parts with the matching `portable-metadata-full.json` or `portable-metadata-jpn.json` and use the merge helper supplied with that package. GitHub Source code archives and update ZIPs do not include a complete runtime.

## Settings and outputs

- Choose Chinese, Japanese or English under **Settings and updates → Appearance → Interface language**. Changes apply on the next launch. Lyric recognition is set separately in Preparation.
- Appearance also offers light/dark themes, Moonlight glass/solid panels and interface scaling.
- Agent/MCP is an optional, separate technical preview that exposes 12 typed top-level tools to a compatible host. It does not add a general command executor. See the [MCP tool reference (Chinese)](docs/AgentMCPStandard.zh-CN.md).
- Choose automatic, direct or supported accelerated downloads in Settings or Models. See [download sources](docs/DownloadSources.en.md) and [interface languages](docs/InterfaceLanguages.en.md).
- Preparation supports lead/backing tracks, reference lyrics, Chinese characters/pinyin, optional pitch curves and manual BPM. Select MIDI, editor projects and audio separately; set the save folder in Output.
- Edit timed lyrics before exporting SRT/WebVTT. Text without timing is not automatically divided into subtitles.
- Manage caches in Settings. Task outputs, model weights and sign-in sessions are protected from ordinary cache cleanup.

## Simple mixing and audition

Simple mixing is an independent workflow for up to three vocal WAVs rendered by your singing editor and one accompaniment. Adjust track level, pan and mute/solo; vocal timing and reverb sends; and shared vocal-bus EQ, compression and reverb. Presets and editable A/B segments are starting points for your own listening.

Continuous preview renders short segments and can advance across their boundaries. After an edit, VWA keeps existing playback going when it can and switches to the new result when ready. If the audio output device fails, it retries once with the compatible built-in player; if playback still cannot start, it offers the current segment in the system player. Buffered segment changes and player handoff can differ briefly from a full export, so check seams, timing, reverb tails and device behaviour on the complete song. Levels are sample peaks and gain reduction is an estimate.

On first use, install the pinned mixing component from the mixing dialog. VWA downloads and verifies the optional Pedalboard 0.9.26 Windows x64 component under `dependencies/optional/pedalboard/current`; it does not alter the base runtime. The source files are never overwritten. Export a new WAV or explicitly register it as a task Mixdown before video assembly.

## Video and publishing

Video assembly is optional. Choose rendered audio and a video, align them if needed, then create the finished video.

In Publishing, choose the finished video and source task, review the cover and credits, then prepare the selected platform. Original Bilibili covers come from metadata; custom images and video frames are separate choices. Cover fitting keeps the full image with padding for 4:3/16:9 when needed.

Review the creator page and confirm publication manually. Keep the finished video in place until upload; regenerate publication files after moving or changing it.

## Updates and diagnostics

Use Settings and updates only when it explicitly offers 1.1.0 for your exact installed version. A compatible update preserves settings, tasks, sign-in sessions, installed models and optional components. Back up projects you are editing before upgrading. Do not unpack an update archive over an installation.

For 1.1.0, the 1.0.0 update route is required by the release gate. 0.4.0/0.4.1 eligibility remains pending until frozen-client, safety, rollback and end-to-end tests pass. Older `VWA-Updater.exe` downloads and `BVWA` archives from 1.0.0 are not a substitute for the 1.1.0 update route.

Keep `dependencies`, `models`, `assets`, `components` and `vendor` with the executable. Startup errors are recorded in `launcher.log`. `tools/Start-Diagnostics.bat` shows startup diagnostics; `tools/Check-Dependencies.bat` checks runtime files. In JPN, add the Chinese model from Models when needed.

Project code: [MIT license](LICENSE). Third-party terms: [notices](THIRD_PARTY_NOTICES.md). [Project repository](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant).
