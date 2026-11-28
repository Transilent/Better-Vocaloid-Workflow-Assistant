<p align="center"><img src="assets/app.svg" width="80" alt="VWA icon"></p>

# VWA 3.0.0 — Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md)

An end-to-end Windows 10/11 x64 desktop assistant for song preparation, lyric-driven tuning, multi-track chorus pitch correction, safe 12-format project conversion, 16-track native mixing, video assembly, and publishing prep.

## Start

1. **Get VWA**: Download **`VWA-Online-Installer-3.0.0-x64.exe`** from [Releases](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest) (recommended: pick Full or JPN visually with mirror racing and multi-threaded resume), or download the portable Full split archive / JPN single ZIP. Extract to a short writable folder and launch **`VWA.exe`** inside the `VWA` folder while keeping companion folders together.
2. **Preparation**: Drop a local song or Bilibili BV/link (batch queue supported), review separation, lyrics, notes, and output options, then select **Start preparation**.
3. **Tasks & results**: Search, filter, resume interrupted tasks, or reprocess with current settings.
4. **Tuning & Project Converter**: Open a task or local SVP/SV2 (`version: 203+` independent groups supported), USTX, VPR, VSQX, ACEP, or UFData in **Tuning** and save non-destructively; use **Project Converter** to safely convert between 12 singing-synthesis formats with readback checks and timed LRC/SRT export.
5. **Chorus Workbench & Simple Mixing**: Polish multi-track vocal stems against reference MIDI in **Chorus Workbench** with native Signalsmith Stretch auditioning, then balance up to 16 tracks in **Simple Mixing** (native WASAPI engine with hot-plug recovery) and export your Mixdown WAV.
6. **Video & Publishing**: Sync video with Mixdown if needed, prepare covers and platform drafts in **Publishing**, and confirm publication manually on the platform site.

> Press `Ctrl+K` for the global Command Palette, `Ctrl+/` for keyboard shortcuts, or drag and drop media/project files onto the window to route directly.

## Choose a package

| Package | Files | Purpose |
| --- | --- | --- |
| Online Installer (Recommended) | `VWA-Online-Installer-3.0.0-x64.exe` | ~139 KB zero-dependency executable; pick Full or JPN, race mirrors, resume chunks, verify SHA-256, and preserve user data on existing folders. |
| Portable Full | `VWA-Windows-x64.zip.001`–`.003` + `portable-metadata-full.json` + `Merge-PortableParts.ps1` | ~4.93 GiB merged; includes Chinese/English Qwen3-ASR weights and complete portable runtime. |
| Portable JPN | `VWA-Windows-x64-JPN.zip` | ~1.55 GiB single ZIP; omits only the three Chinese Qwen3-ASR weights while keeping all studios and Japanese recognition ready. Chinese/English models can be installed later in **Models & components**. |

GitHub Source code archives and bridge ZIPs (`VWA-Program-Update.zip`, `VWA-App-Update-*.zip`, `VWA-Runtime-Upgrade-*.zip`, `BVWA-App-Update.zip`) do not contain a full runtime and must not be extracted manually as standalone installs.

## Settings, models & outputs

- **Appearance & language**: Configure Light/Dark theme, Moonlight glass/solid surfaces, UI scaling, and English / 简体中文 / 日本語 under **Settings & support → Appearance** (applies on next launch). Lyric recognition language is configured independently in Preparation.
- **Optional separation models**: Install PyTorch BS-RoFormer (coexisting CUDA and CPU runtimes sharing weights) under **Models & components**; AMD and other non-NVIDIA GPUs can also opt into the experimental isolated **BS-RoFormer ONNX (WebGPU, `10s` / `20s FP16`)** component (`onnxruntime-webgpu 1.27.0`).
- **Agent / MCP (optional)**: Separate technical preview exposing 12 typed top-level tools without an arbitrary command executor. See [MCP tool reference (ZH)](docs/AgentMCPStandard.zh-CN.md).
- **Download sources & cache**: Pick Auto Race, GitHub Direct, or accelerated mirrors in Settings or Components (see [download sources](docs/DownloadSources.en.md)). Ordinary cache cleanup protects task outputs, models, presets, and sign-in sessions. User QQ group: `490619616`.

## Simple mixing & audition

Simple mixing uses a **16-track native continuous PCM audio engine** supporting track level, pan, mute/solo, vocal alignment, reverb sends, shared vocal-bus EQ/de-esser/compressor/limiter/reverb, loudness-matched A/B auditioning, WASAPI device enumeration and hot-plug reconnect, 60 fps FBO waveforms, and draft crash recovery. Optional Pedalboard effects install in isolation under `dependencies/optional/pedalboard/current`.

## Updates & diagnostics

- **Upgrading to 3.0.0**: Existing `v2.0.0`, `v2.1.0`, `v1.0.0`, `v1.1.0`, and `v0.4.0` installs can check for updates in-app to enter the zero-dependency local Web UI full-package auto-migrator, which downloads and verifies 3.0.0 and copies `config.json`, `jobs/`, `user-presets/`, `cache/accounts/`, `publish-packages/`, `dependencies/optional/`, and downloaded Qwen weights into the new folder while leaving the old folder untouched. For `v0.3.x` / `v0.4.1`, run `VWA-Online-Installer-3.0.0-x64.exe` pointed at your existing folder for non-destructive upgrade.
- **Folder integrity**: Keep `dependencies`, `runtime/qt6`, `models`, `assets`, `components`, and `vendor` beside `VWA.exe`.
- **Diagnostics**: Check `launcher.log` for startup errors, run `tools/Start-Diagnostics.bat` for startup diagnostics or `tools/Check-Dependencies.bat` to verify runtime files, and use **Settings & support → Local Diagnostics → Copy summary** when reporting issues.

Project code: [MIT license](LICENSE). Third-party terms: [notices](THIRD_PARTY_NOTICES.md) and [v3.0.0 runtime notices](docs/ThirdPartyRuntime.md). [Project repository](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant).
