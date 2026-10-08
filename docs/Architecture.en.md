# Architecture

[English](Architecture.en.md) | [简体中文](Architecture.zh-CN.md) | [README](../README.md)

## Processing flow

```text
Bilibili BV/link ── metadata + download ─┐
                                       ├─ decoded audio ─ separation ─ lyric MIDI
Local music ─────── saved snapshot ─────┘

Manual tuning + video editing ─ finished video ─ covers/credits ─ creator upload
```

For local music, metadata is a placeholder for manually supplied source credits. No Bilibili download or cover lookup is performed. The input snapshot is hashed and retained for resume.

Automatic dual-part processing first estimates combined vocals and accompaniment with the MDX instrumental model. The karaoke model then estimates backing content from the vocal stem; its residual is used as lead vocals. This estimates musical roles rather than singer identities. External full-length stems bypass this separation. Vocal2Midi transcribes each vocal part independently, and the assistant combines their MIDI while preserving timing.

## Code map

| Module | Responsibility |
| --- | --- |
| `launch.py`, `app.py` | Runtime bootstrap, desktop controls, background jobs |
| `common.py`, `check_dependencies.py` | Relative paths, configuration, subprocess cancellation, dependency checks |
| `pipeline.py` | Input snapshots, metadata, downloads, stages, resume |
| `mdx.py`, `separate_audio.py` | ONNX separation and local separation CLI |
| `v2m_runtime.py`, `midi_bridge.py` | Bundled transcription integration |
| `midi_checks.py`, `midi_merge.py` | Review hints, MIDI validation and track assembly |
| `cover_art.py`, `publication.py` | Original covers, padded variants, publication bundles |
| `publishing_ui.py`, `publish_browser.py` | Platform sign-in and upload preparation |
| `publish-selectors.json` | Platform page selectors |
| `tools/build_portable.py` | Inventory-based ZIP creation and splitting |
| `tools/Install-Runtime.ps1` | Source-checkout bootstrap, Release downloads and dependency integrity |

## Files and state

`config.json` stores application settings. Paths in a clean release resolve relative to the extracted application folder. `dependencies/manifest.json` records fixed runtime files and SHA-256 hashes; `source-files.json` selects repository files for packaging. A generated ZIP also includes `release-inventory.json` for all packaged files.

Runtime state is separate: `jobs/` stores stage inputs/results, `cache/` stores temporary data and dedicated browser profiles, and `publish-packages/` stores finished-video snapshots and publication text. These folders can contain private data and are excluded from Git and clean releases. Do not distribute a working application folder without removing its runtime state.

Resume validates expected stage outputs and relevant settings. Separate vocal transcription results can be reused. A changed source snapshot or damaged output requires rebuilding the affected stage.

## Automation boundary

Inference runs in local subprocesses. Website automation uses Playwright selectors with dedicated browser profiles. Upload preparation leaves the creator page open for review and does not click the final publish control. Page changes can invalidate selectors. Manual tuning and final audio/video assembly remain outside this assistant.
