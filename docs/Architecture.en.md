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

The sidebar begins with Preparation workflow, Tuning workflow, Video assembly and Publishing, followed by Models/components, Tasks/results and Settings/updates. Preparation uses full-width expandable steps with a device dialog in the toolbar. Independent tuning takes an existing task or imported SVP, snapshots its source, composes one exclusive expression source with optional style/pitch overlays in memory, then finalizes density and commits one candidate. Existing manual calibration fitting and compatible task exports use the same engine. See [Tuning workflow](TuningWorkflow.en.md) for format limits and recovery. Publishing uses four scrollable pages with bottom navigation and a final action outside the scroll area. The cover widget rescales without cropping. Cache management remains in Settings/updates. Legacy graph serialization remains readable, and processing runs in isolated children. Only project conversion loads LibreSVIP's separate dependency directory.


Japanese transcription uses the original bundled Romaji ASR weights. The integration preserves supported standalone phones at slice boundaries, checks HubertFA input phonemes, isolates recoverable alignment data errors by chunk, and records pitch-only fallbacks separately from empty ASR. Short-mora boundary repair is limited to 150 ms gaps in Japanese. Non-singing mora tokens consume their kana display token so subsequent syllables stay aligned. Chinese ASR selection remains Qwen.

The VWA appearance uses light/dark semantic palettes, shared Qt styles and a cached application-internal background. Radio-card adapters observe existing combo values and model changes. Appearance preferences merge into their workspace namespace. The desktop retains a standard system frame; no native Windows backdrop API is enabled. See [workspace behavior](Frontend.en.md).

## Code map

| Module | Responsibility |
| --- | --- |
| `launch.py`, `app.py` | Runtime bootstrap, desktop controls, background jobs |
| `desktop_theme.py`, `workspace_ui.py`, `publishing_layout.py` | Theme, sidebar workspace and publication form |
| `workflow_widgets.py` | Step navigation and proportional cover preview |
| `components_ui.py`, `optional_components.py`, `components/roformer.json` | Download UI, pinned install manifest, resumable installer and validation |
| `roformer.py` | Optional Pymss inference and timeline-preserving lead/backing outputs |
| `common.py`, `check_dependencies.py` | Relative paths, configuration, subprocess cancellation, dependency checks |
| `pipeline.py` | Input snapshots, metadata, downloads, stages, resume |
| `workflow.py`, `processing_workflow.py`, `processing_ui.py` | Preset migration and ordered processing cards |
| `tuning_ui.py`, `tuning_workflow.py`, `tuning_engine.py` | Independent tuning UI, immutable source sessions, exclusive parameter-source composition, one final density pass and protected candidate commits |
| `progress_state.py`, `error_recovery.py` | Subtask events, elapsed time, failure classification and CPU retry |
| `task_store.py`, `results_ui.py` | Task metadata, search/filter UI, stale-run detection and bounded deletion |
| `storage.py`, `storage_ui.py` | Disposable-location allowlist, asynchronous scan and confirmed cleanup |
| `subtitles.py`, `subtitles_ui.py` | Timed lyrics, MIDI tempo conversion, cue editing and SRT/WebVTT |
| `mdx.py`, `separate_audio.py` | ONNX separation and local separation CLI |
| `v2m_runtime.py`, `midi_bridge.py` | Bundled transcription integration |
| `midi_checks.py`, `midi_merge.py` | Review hints, MIDI validation and track assembly |
| `cover_art.py`, `publication.py` | Original covers, padded variants, publication bundles |
| `publishing_ui.py`, `publish_browser.py` | Platform sign-in and upload preparation |
| `publish-selectors.json` | Platform page selectors |
| `tools/build_portable.py` | Inventory-based ZIP creation and splitting |
| `tools/Install-Runtime.ps1` | Frozen legacy source-checkout path; consumes the BVWA-root compatibility metadata and Release ZIP |

## Files and state

`config.json` stores application settings. Paths in a clean release resolve relative to the extracted application folder. `dependencies/manifest.json` records fixed runtime files and SHA-256 hashes. `source-files.json` inventories the repository; `portable-files.json` selects release runtime files and maps the concise bilingual guides to the package README files. A generated ZIP also includes `release-inventory.json` for all packaged files.

Canonical VWA packages are identified by `portable_downloads.full` and `portable_downloads.jpn`; their split parts use matching per-variant portable metadata. The top-level `release-assets.json` `archive` and `parts` fields retain a BVWA-root compatibility full ZIP for the frozen `Install-Runtime.ps1` path. Tests, screenshots and build materials are kept in the source repository. Dependency test directories and the unused upstream desktop GUI are excluded from the runtime inventory. Diagnostic batch files are placed in `tools`; a fresh package has one canonical launcher, `VWA.exe`.

Runtime state is separate: `jobs/` stores stage inputs/results, `cache/` stores temporary data and dedicated browser profiles, and `publish-packages/` stores finished-video references, cover assets and publication text. These folders can contain private data and are excluded from Git and clean releases. Do not distribute a working application folder without removing its runtime state.

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

Inference runs in local subprocesses. Website automation uses Playwright selectors with dedicated browser profiles. Upload preparation leaves the creator page open for review and does not click the final publish control. Page changes can invalidate selectors. Tuning remains manual. Optional video assembly has a separate page and is not required for publishing a finished video.

## Program updates

`download_sources.py` routes public downloads. `updater.py` discovers stable VWA releases, checks runtime compatibility and stages an allowlisted program archive. `updates_ui.py` manages download settings and progress. After the desktop and `VWA.exe` exit, `tools/update_apply.py` replaces verified files with rollback on failure. Configuration, task files, browser profiles and model environments are outside the update allowlist. `tools/build_app_update.py` produces the small program archive after confirmed user testing.

Task outputs default to `jobs/` and may use a custom parent directory. The selected parent and previously used task parents are kept in private `cache/workspace.json`, enabling task discovery across locations. Requests record their output parent; resuming uses the actual existing task path, while reprocessing creates a new task under the selected parent.

Project export merges native Vocal2Midi USTX voice tracks with LibreSVIP, preserves original lyric text and pitch curves, and reads files back to validate serialization. Native files and final exports are checked on resume. NetEase lyric timing is used only by the subtitle editor, while original lyric text is passed to Vocal2Midi forced alignment. HTTP Range downloads use bounded workers and atomic SHA-256 validation before activation.

Reference lyrics and subtitle matching keep independent sources and drafts. New GUI tasks select the v2 backend; existing requests retain their recorded backend. NetEase references target lead vocals, and subtitle imports do not rewrite transcription references. The subtitle UI accepts NetEase timelines or MIDI lyrics; untimed text is rejected at conversion. Source choices enter tasks/presets, while lyric contents stay outside presets.

## Runtime and storage boundaries

Stage receipts bind downloaded inputs and audio to their content hashes and relevant parameters. Audio checks include sample rate, channels, frame count and finite PCM values. Transcription signatures contain recognition inputs and the effective model/settings selection; theme and final converter format choices are excluded. Historical task receipts migrate after validation.

A sequential worker reuses native inference sessions within one CPU/Japanese task. Chinese GPU tasks keep separate voice workers to release resident models before the next ASR stage. Retained Chinese CPU ASR additionally requires a measured host-memory margin. DirectML adapters and optional CPU thread limits are shared across the inference engines.

Exports commit and validate each output separately. Retrying one damaged format preserves other outputs. Multiple audio encoders share one decode per stem. Separation/import output buffers and silence scans use bounded blocks. Range downloads write into one partial file plus a small range receipt; the final SHA-256 check precedes activation.

The task library scans on a background thread and caches metadata by file size/mtime. Disk enumeration uses non-following directory entries. Cleanup rejects links, active tasks/uploads/installers and cache ancestors of registered custom task directories.

Startup checks the core runtime. Tasks check the features they use. Japanese Romaji ASR and notes-only extraction do not require Qwen weights. Chinese originals have a pinned repair/download entry in Models/components. Native ASR DLLs are loaded from their existing directory. Browser and language dependency plans can be inspected with `tools/build_portable.py --plan-profile`; these plans do not create or publish an archive.

The [update protocol](UpdateProtocol.en.md) keys current routes by source runtime hash. Its frozen first hop contains exactly three files and stages the matching runtime/program delta before replacement. Released 0.4.0/0.4.1 clients retain their fixed BVWA-named legacy projections; older runtime routes can use the canonical `VWA-Updater.exe`, with optional byte-identical BVWA bootstrap aliases where supplied. Private state from released clients remains readable, and the application takes both legacy and canonical launcher locks together. Fresh packages contain only `VWA.exe`; an eligible upgraded installation may retain a hidden `BVWA.exe` shortcut alias.

## Task state and artifact evidence

`task_contract.py` indexes producer receipts without replacing their inference or export cache checks. `status.json` describes the actual execution plan; human-completed projects, Mixdowns, videos and publication preparation have separate handoff records. File dependencies also protect live inputs during cache cleanup. See [Task contracts](TaskContracts.en.md) for ownership, migration and recovery rules.
