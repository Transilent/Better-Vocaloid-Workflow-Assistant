<p align="center">
  <img src="assets/app.svg" width="84" alt="VWA icon">
</p>

<h1 align="center">VWA · Vocaloid Workflow Assistant</h1>

<p align="center">
  <strong>End-to-end Windows desktop studio for singing-synthesis workflows</strong><br>
  Song Preparation · Lyric-Driven Tuning · Multi-Track Chorus Pitch Correction · 12-Format Project Converter · 16-Track Native Mixing · Video & Publishing
</p>

<p align="center">
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest">
    <img src="https://img.shields.io/github/v/release/Transilent/Better-Vocaloid-Workflow-Assistant?label=Release&color=3b6ce8" alt="Latest Release">
  </a>
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/actions/workflows/ci.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/Transilent/Better-Vocaloid-Workflow-Assistant/ci.yml?branch=main&label=CI" alt="CI Status">
  </a>
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/actions/workflows/codeql.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/Transilent/Better-Vocaloid-Workflow-Assistant/codeql.yml?branch=main&label=CodeQL" alt="CodeQL">
  </a>
  <img src="https://img.shields.io/badge/Platform-Windows%2010%2F11%20x64-0078D6" alt="Windows x64">
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/Transilent/Better-Vocaloid-Workflow-Assistant?color=43c58e" alt="MIT License">
  </a>
  <img src="https://img.shields.io/badge/QQ%20Community-490619616-12B7F5" alt="QQ Group 490619616">
</p>

<p align="center">
  <strong>English</strong> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.ja.md">日本語</a>
</p>

<p align="center">
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest"><strong>Download v3.0.0</strong></a> ·
  <a href="docs/UserGuide.en.md">User Guide</a> ·
  <a href="docs/Portable.en.md">Portable Guide</a> ·
  <a href="CHANGELOG.md">Changelog</a> ·
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose">Report an Issue</a>
</p>

---

## Contents

- [What's New in v3.0.0](#whats-new-in-v300)
- [Workspaces & Capabilities](#workspaces--capabilities)
- [Download & Installation](#download--installation)
- [Upgrading from Older Versions](#upgrading-from-older-versions)
- [Quick Start](#quick-start)
- [Hardware & GPU Acceleration](#hardware--gpu-acceleration)
- [Documentation Map](#documentation-map)
- [Community & Support](#community--support)
- [Acknowledgements & License](#acknowledgements--license)

---

## What's New in v3.0.0

- **Multi-Track Chorus & Pitch Workbench**: Compare dry vocal stems against reference MIDI, review phrase by phrase, draw precise pitch curves, tweak per-syllable dynamics, audition with native high-fidelity Signalsmith Stretch pitch shifting (8 kHz tonality & formant preservation), and export corrected stems.
- **Safe 12-Format Project Converter**: Convert between 12 singing-synthesis formats (SVP/SV2, USTX, VPR, VSQX, ACEP, UFData, MIDI, etc.) with track splitting, readback health checks, and timed LRC/SRT lyric export—source files are never overwritten.
- **16-Track Native Real-Time Mixdown Engine**: Upgraded from 3 vocal tracks to a full 16-track native WASAPI engine with device enumeration, hot-plug auto-reconnect, native limiter/output processing, 60 fps FBO waveforms, and draft crash recovery.
- **Dual-Runtime BS-RoFormer + Experimental AMD WebGPU ONNX**: Coexisting PyTorch CUDA and CPU runtimes sharing one weight set, plus an opt-in isolated **BS-RoFormer ONNX (`10s FP16` / `20s FP16`)** component (`onnxruntime-webgpu 1.27.0`, credited to `@AU-ki`) mirrored directly on this repository.
- **Zero-Dependency Online Installer & 2-Hop Auto-Migrator**: New 139 KB single-file `VWA-Online-Installer-3.0.0-x64.exe` (ZH/EN/JA, Dark/Light themes, Full/JPN cards, mirror racing, multi-threaded Range resume, non-destructive upgrade), plus seamless in-app 2-hop migration from v0.4.0, v1.x, and v2.x.
- **UX & Security Hardening**: 4-scenario onboarding, Command Palette (`Ctrl+K`), Shortcut Cheat Sheet (`Ctrl+/`), drag-and-drop workspace routing, batch preparation queue, clearer component status badges, upgraded portable Python dependencies, and zero open security alerts.

---

## Workspaces & Capabilities

| Workspace | Capabilities |
| --- | --- |
| **Preparation** | Import local audio or Bilibili BV/links (with batch queue), separate lead vocals, backing vocals, and accompaniment, transcribe Chinese, Japanese, or English lyrics and notes, and export MIDI, USTX, VSQX, or SVP with optional manual BPM and continuous pitch curves. |
| **Tuning** | Open task projects or local SVP/SV2 (`version: 203+` independent groups supported), USTX, VPR, VSQX, ACEP, or UFData. Navigate by lyric, edit notes/lyrics, draw pitch, apply reference-vocal mapping, measured voicebank calibration, or Chinese-opera styling, and save non-destructively. |
| **Chorus Workbench** | Multi-track vocal stem pitch/timing correction against reference MIDI with phrase review, hand-drawn pitch, syllable gain, native Signalsmith Stretch auditioning, and stem delivery. |
| **Project Converter** | Standalone safe converter across 12 singing-synthesis project formats with track splitting, post-convert readback verification, and timed LRC/SRT lyric export. |
| **Simple Mixing** | Up to 16-track native continuous PCM mixer with level, pan, mute/solo, vocal alignment, dedicated reverb return, shared vocal-bus EQ/de-esser/compressor/limiter, A/B loudness-matched preview, and WASAPI hot-plug recovery. |
| **Video Assembly** | Combine original video with your Mixdown with automatic alignment, manual offset, and optional loudness processing. |
| **Publishing** | Prepare covers, titles, descriptions, credits, and platform drafts (Bilibili assisted upload; Xiaohongshu local handoff package with copy helpers and AI-content disclosure checklist). |
| **Tasks & Results** | SQLite-backed task index with search, filtering, resume/retry, artifact lineage checks, raw logs, and one-click local diagnostic summaries. |
| **Models & Components** | Manage Qwen3-ASR (Chinese/English), BS-RoFormer (PyTorch CUDA/CPU coexistence + opt-in ONNX WebGPU), and Pedalboard with mirror racing, SHA-256 verification, and clear active-tier badges. |
| **Settings & Support** | Light/Dark themes, Moonlight glass/solid surfaces, ZH/EN/JA UI languages, scaling, [local preset library](docs/PresetLibrary.en.md), safe cache cleanup, and in-app update checks. |
| **Agent / MCP (Optional)** | Separate optional technical preview exposing 12 typed top-level tools for compatible hosts without adding an arbitrary command executor. See [MCP tool reference (ZH)](docs/AgentMCPStandard.zh-CN.md). |

> **Productivity Shortcuts**: Press `Ctrl+K` anywhere to open the Command Palette, `Ctrl+/` for the shortcut reference, or drag and drop audio, video, or project files onto the window to route directly to the right workspace.

---

## Download & Installation

Get the latest files on the [**VWA v3.0.0 Release Page**](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest). Every package supports English, Simplified Chinese, and Japanese UI (configured independently from lyric-recognition language):

| Method | Best For | Files | Highlights |
| --- | --- | --- | --- |
| **⭐ Online Installer (Recommended)** | New users & non-destructive upgrades | `VWA-Online-Installer-3.0.0-x64.exe` (~139 KB) | Single zero-dependency Windows 10/11 executable. Pick **Full** or **JPN** and target folder visually; auto mirror racing, multi-threaded HTTP Range resume, SHA-256 verification, and automatic user-data preservation on existing folders. |
| **Portable Full** | Offline archival / complete out-of-the-box setup | `VWA-Windows-x64.zip.001`–`.003` + `portable-metadata-full.json` + `Merge-PortableParts.ps1` (~4.93 GiB merged) | Includes Chinese/English Qwen3-ASR weights and full portable runtime; extract and run `VWA.exe`. |
| **Portable JPN** | Japanese/notes-only or compact single download | `VWA-Windows-x64-JPN.zip` (~1.55 GiB, single file) | Omits only the three Chinese Qwen3-ASR weights; all studios and Japanese recognition work immediately, and Chinese/English models can be added later in **Models & Components**. |

> **Note**: GitHub's auto-generated **Source code** archives and bridge ZIPs (`VWA-Program-Update.zip`, `VWA-App-Update-*.zip`, `VWA-Runtime-Upgrade-*.zip`, `BVWA-App-Update.zip`) are **not** standalone installations.

### Option 1: Online Bootstrapper Installer (Easiest)

1. Download and run **`VWA-Online-Installer-3.0.0-x64.exe`**.
2. Choose **Full Edition** or **JPN Compact Edition** and select a short writable folder such as `D:\Apps\VWA` (pointing to an existing VWA folder automatically enables non-destructive upgrade mode).
3. Click **Download & Install**—parts are downloaded in parallel, verified by SHA-256, extracted, and ready to launch.

### Option 2: Portable ZIP

- **JPN Edition**: Download and extract `VWA-Windows-x64-JPN.zip`, then launch **`VWA.exe`** inside the extracted `VWA` folder.
- **Full Edition**: Place `VWA-Windows-x64.zip.001`, `.002`, `.003`, `portable-metadata-full.json`, and `Merge-PortableParts.ps1` in one folder and run in PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Merge-PortableParts.ps1
```

Extract the verified `VWA-Windows-x64.zip` and run **`VWA.exe`**. Verify any downloaded release file against `SHA256SUMS.txt`.

---

## Upgrading from Older Versions

| Current Version | Upgrade Path | Data & Model Preservation |
| --- | --- | --- |
| **`v2.0.0` / `v2.1.0`** | Open **Settings & Support → Check for updates** and follow the restart prompt to launch the zero-dependency 3.0.0 Full-Package Downloader & Auto-Migrator Web UI. | Automatically migrates `config.json`, `jobs/`, `user-presets/`, `cache/accounts/`, `publish-packages/`, `dependencies/optional/`, and downloaded Qwen weights; original folder stays untouched for rollback. |
| **`v1.0.0` / `v1.1.0`** | Open **Settings & Updates → Check for updates**; the two-stage bridge automatically launches the 3.0.0 Auto-Migrator Web UI. | Same full non-destructive migration into the new folder. |
| **`v0.4.0`** | Supports in-app 2-hop migration to 3.0.0, or use the Online Installer directly. | Same non-destructive migration. |
| **`v0.3.x` / `v0.4.1`** | Run **`VWA-Online-Installer-3.0.0-x64.exe`** and point it to your existing folder for non-destructive in-place upgrade (or install to a new folder and copy `jobs/`, `config.json`, `user-presets/`). | Preserves config, jobs, presets, sign-in sessions, and installed optional models. |

> Close running VWA instances and back up any editor projects you are actively modifying before upgrading.

---

## Quick Start

1. **Prepare**: Drop a local song or Bilibili BV/link into **Preparation**, pick separation mode, lyric language, and output folder, and run to get stems, MIDI, and editor projects.
2. **Tune & Convert**: Open a task or local SVP/SV2/USTX/VPR/VSQX/ACEP/UFData project in **Tuning** to edit lyrics, notes, styles, or pitch curves and render dry vocals in your singing editor. Use **Project Converter** whenever you need safe 12-format cross-editor conversion.
3. **Polish & Mix**: Fine-tune multi-track vocal stems in **Chorus Workbench**, then balance up to 16 tracks in **Simple Mixing** with live EQ, de-essing, compression, reverb, and A/B auditioning.
4. **Assemble & Publish**: Sync video with your Mixdown in **Video Assembly**, prepare covers and metadata in **Publishing**, and confirm publication manually on the target platform.

---

## Hardware & GPU Acceleration

- **OS**: Windows 10 / 11 x64. Portable packages and the Online Installer bundle an isolated Python and Qt 6.10.3 runtime (no system Python required). A compatible singing editor and voicebank are required for voice synthesis.

| GPU Vendor | Recommended Backend | Notes |
| --- | --- | --- |
| **NVIDIA GPUs** | Base DirectML + optional **BS-RoFormer (PyTorch CUDA)** | Fastest overall performance; DirectML and CUDA devices are configurable per engine. |
| **AMD / Intel GPUs & APUs** | Base DirectML + optional **BS-RoFormer ONNX (Experimental WebGPU)** | Opt-in isolated runtime in **Models & Components**: choose `10s FP16` for ≤8 GB VRAM / APUs, or `20s FP16` for ≥12 GB VRAM. |
| **CPU / Driver Reset Fallback** | CPU mode | Automatically falls back to CPU on DirectML device-removed resets (e.g., `0x887A0005`), or can be selected manually anytime. |

---

## Documentation Map

| Category | Guides |
| --- | --- |
| **Getting Started** | [User Guide](docs/UserGuide.en.md) · [Portable Guide](docs/Portable.en.md) · [Startup & Tour (ZH)](docs/StartupExperience.zh-CN.md) · [Preset Library](docs/PresetLibrary.en.md) |
| **Core Workflows** | [Tuning Guide](docs/TuningWorkflow.en.md) · [Chorus Workbench (ZH)](docs/ChorusWorkspace.zh-CN.md) · [Simple Mixing Component](docs/SimpleMixingComponent.en.md) · [Style Presets](docs/StylePresets.en.md) |
| **Models, Network & Updates** | [Download Sources](docs/DownloadSources.en.md) · [AMD WebGPU Guide (ZH)](docs/AmdGpuAdaptation.zh-CN.md) · [Update Protocol](docs/UpdateProtocol.en.md) · [Interface Languages](docs/InterfaceLanguages.en.md) |
| **Advanced & Contributing** | [MCP Tool Reference (ZH)](docs/AgentMCPStandard.zh-CN.md) · [Architecture](docs/Architecture.en.md) · [Contributing Guide](CONTRIBUTING.md) · [Security Policy](SECURITY.md) |

---

## Community & Support

- **User Community**: QQ Group **`490619616`**
- **Bug Reports & Feature Requests**: [GitHub Issues](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose) — start by clicking **Settings & Support → Support & Logs → Local Diagnostics → Copy summary** and review [SUPPORT.md](.github/SUPPORT.md) to redact private paths or credentials.

---

## Acknowledgements & License

Thanks to the authors and contributors of the following projects:

| Project / Contributor | Use in VWA |
| --- | --- |
| [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi) | Note transcription and lyric alignment. |
| [LibreSVIP](https://github.com/SoulMelody/LibreSVIP) | Multi-format singing-synthesis project I/O and conversion. |
| [Signalsmith Stretch](https://github.com/Signalsmith-Audio/signalsmith-stretch) | Native high-fidelity continuous pitch shifting with formant preservation for Chorus Workbench. |
| [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) / [UVR models](https://github.com/TRvlvr/model_repo) | Vocal separation, model weights, and metadata. |
| [Pymss](https://github.com/pymss-project/pymss) / [becruily BS-RoFormer karaoke](https://huggingface.co/becruily/bs-roformer-karaoke) | Optional lead/backing separation engine and model. |
| [@AU-ki](https://github.com/AU-ki) | BS-RoFormer ONNX export (`tools/export_roformer_onnx.py`) and cross-vendor WebGPU validation (RX 9070 XT, Arc 130V, RTX 2080Ti, etc.). |
| [Pymss Studio](https://github.com/pymss-project/pymss-studio) | Interface and model-management design reference. |
| [PyWORLD](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder) / [WORLD](https://github.com/mmorise/World) | Reference-vocal acoustic analysis. |
| [163MusicLyrics](https://github.com/jitwxs/163MusicLyrics) | Lyric-lookup behavior reference. |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Media downloading. |
| [FFmpeg](https://ffmpeg.org/) / [Jellyfin FFmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) | Audio/video processing. |
| [Playwright for Python](https://github.com/microsoft/playwright-python) | Browser sign-in and upload assistance. |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) / [PyTorch](https://pytorch.org/) | Model execution runtimes. |
| [Qt Quick and PySide6](https://doc.qt.io/qtforpython-6.10/) | Desktop interface framework. |
| [NumPy](https://numpy.org/), [SciPy](https://scipy.org/), [librosa](https://librosa.org/), [SoundFile](https://github.com/bastibe/python-soundfile), [Mido](https://github.com/mido/mido), [Pillow](https://python-pillow.org/) | Numeric, audio, MIDI, and image processing. |

The integrated Vocal2Midi pipeline also uses [GAME](https://github.com/openvpi/GAME), [HubertFA](https://github.com/wolfgitpr/HubertFA), [LyricFA](https://github.com/wolfgitpr/LyricFA), [RomajiASR](https://github.com/Xiantaidu/RomajiASR), [llama.cpp](https://github.com/ggml-org/llama.cpp), and related speech and pitch models. See [upstream acknowledgements](dependencies/vocal2midi2/ACKNOWLEDGEMENTS.md) for further credits.

- **License**: VWA's original code is licensed under [MIT](LICENSE). Third-party code, runtimes, and model weights retain their own licenses and notices; see [Third-party notices](THIRD_PARTY_NOTICES.md) and [v3.0.0 runtime notices and corresponding source](docs/ThirdPartyRuntime.md).
