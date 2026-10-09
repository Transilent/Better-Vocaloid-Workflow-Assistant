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

The optional BS-RoFormer route uses the same first stage. Its original checkpoint
estimates lead from combined vocals; subtracting lead from those vocals gives
backing. It does not treat low lead energy as a reason to swap parts. Chinese and
Japanese use identical separation parameters: 44.1 kHz stereo, 20-second chunks,
75% overlap, batch size 1, no TTA and no independent stem normalization.

The native Qt workspace has four sidebar pages. Advanced controls and logs are
folded; task recovery is in Results. Publication uses one primary action whose
state depends on whether the prepared bundle matches the current form.

Japanese transcription uses the original bundled Romaji ASR weights. The integration preserves supported standalone phones at slice boundaries, checks HubertFA input phonemes, isolates recoverable alignment data errors by chunk, and records pitch-only fallbacks separately from empty ASR. Short-mora boundary repair is limited to 150 ms gaps in Japanese. Non-singing mora tokens consume their kana display token so subsequent syllables stay aligned. Chinese ASR selection remains Qwen.

## Code map

| Module | Responsibility |
| --- | --- |
| `launch.py`, `app.py` | Runtime bootstrap, desktop controls, background jobs |
| `desktop_theme.py`, `workspace_ui.py`, `publishing_layout.py` | Theme, sidebar workspace and publication form |
| `components_ui.py`, `optional_components.py`, `components/roformer.json` | Download UI, pinned install manifest, resumable installer and validation |
| `roformer.py` | Optional Pymss inference and timeline-preserving lead/backing outputs |
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

`config.json` stores application settings. Paths in a clean release resolve relative to the extracted application folder. `dependencies/manifest.json` records fixed runtime files and SHA-256 hashes. `source-files.json` inventories the repository; `portable-files.json` selects release runtime files and maps the concise bilingual guides to the package README files. A generated ZIP also includes `release-inventory.json` for all packaged files.

Release assets contain three ZIP parts, the merge script and a minimal archive/part
checksum inventory. Tests, screenshots and build materials are kept in the source
repository. Dependency test directories and the unused upstream desktop GUI are
excluded from the runtime inventory. Diagnostic batch files are placed in `tools`;
the root has one application launcher.

Runtime state is separate: `jobs/` stores stage inputs/results, `cache/` stores temporary data and dedicated browser profiles, and `publish-packages/` stores finished-video snapshots and publication text. These folders can contain private data and are excluded from Git and clean releases. Do not distribute a working application folder without removing its runtime state.

Resume validates expected stage outputs and relevant settings. Separate vocal transcription results can be reused. A changed source snapshot or damaged output requires rebuilding the affected stage.

Optional files reside under `dependencies/optional/bs-roformer/current` and are
excluded from Git and the base archive. CPU/CUDA wheels use a private import path
in inference subprocesses, leaving the base ONNX runtime unchanged. Downloads
verify pinned sizes and SHA-256. A staged environment must pass original-model
loading and an available-device computation before activation. Successful
installation removes downloaded archives. Cancellation retains downloads for
retry; the active environment remains usable. Only one optional runtime is active.
The PyTorch payload omits C++ headers and CMake development files, retaining
Python inference files, DLLs and license metadata.

## Automation boundary

Inference runs in local subprocesses. Website automation uses Playwright selectors with dedicated browser profiles. Upload preparation leaves the creator page open for review and does not click the final publish control. Page changes can invalidate selectors. Manual tuning and final audio/video assembly remain outside this assistant.
