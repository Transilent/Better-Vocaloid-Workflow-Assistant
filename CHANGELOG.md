# Changelog

## 0.4.1 — 2026-10-14

### Fixed

- Honor the pitch-curve switch in all three project formats, including native intermediate projects. With pitch disabled, omit explicit curves and flat tuning presets while retaining notes, lyrics and accompaniment. Switching the option invalidates the project cache.
- Respect the maximum audio slice length when merging tiny vocal fragments, including intervening silence. Retain fragments that cannot fit, with their original timestamps. Apply the correction to both extraction backends.
- Surface the underlying subprocess exception and localized DirectML device-reset codes instead of only the exit-code summary. Normal provider-selection logs no longer classify unrelated failures as GPU errors.
- Preserve the selected download source after restarting into the upgrade window. Both upgrade stages support GitProxy, source fallback, resume and SHA-256 verification.
- Write command-line startup diagnostics to `diagnostics.log`, avoiding a log-file conflict with an already running desktop window.

### Updating

- Provide separate compatible upgrade paths for v0.3.1 and v0.4.0. Existing models, settings, tasks, sign-in profiles and optional components are retained.
- Publish update archives only. New installations can use the v0.4.0 portable package, then update through Settings/updates.

## 0.4.0 — 2026-10-13

- Support verified in-place online upgrades from v0.3.0/v0.3.1 using a compatible bootstrap and a small runtime delta; retain models, tasks and sign-in state.

- Rename the main pages to Tuning workflow and Publishing workflow, keeping them together in the sidebar.
- Guide publication through four full-page steps with larger cover previews, visible platform drafts/account controls and a fixed final action.
- Check required inputs before advancing, clear stale cover previews and show platform-specific status.
- Recheck publication prerequisites when navigating by step number; recover from missing source tasks and show clear restoration errors.
- Disable project formats, pitch-curve and accompaniment options when project export is off, preserving selections for re-enabling.
- Enlarge workflow controls and use opaque light/dark color themes with a restrained green accent and standard system window controls.
- Move per-engine devices to the tuning toolbar while preserving preset settings.

- Use Vocal2Midi 2.0 for new GUI tasks without a backend selector.
- Separate reference calibration into NetEase/local sources, with NetEase lyrics applied to the lead vocal.
- Split subtitle matching into NetEase timelines and MIDI lyric tracks; remove untimed text estimation.

- Merge workflow editing and music processing into compact expandable cards; embed subtitles and move cache management into settings.
- Add dark/light themes and reusable processing/export presets.
- Integrate the Vocal2Midi 2.0 application layer with existing Chinese/Japanese models and shared Python/FFmpeg.
- Add NetEase song/artist search, song links/IDs and lyric-file reference import.
- Export USTX, VSQX and SVP projects with pitch curves, plus WAV/FLAC/MP3/OGG stems.
- Add verified resumable parallel downloads with configurable connections and source fallback.
- License original assistant source under MIT and retain upstream notices.
- Deduplicate native ASR binaries; keep new core export libraries separate from the main runtime.
- Validate stage caches against their inputs and output hashes; repair damaged exports without regenerating intact formats.
- Retrieve publication metadata and original covers without downloading the full source video.
- Reduce intermediate audio allocations with block processing and shared decoding for multiple output codecs.
- Write resumable parallel downloads to one temporary file, avoiding duplicate range assembly.
- Reuse native inference sessions sequentially where appropriate while retaining per-voice isolation for Chinese GPU transcription.
- Add a DirectML adapter selector, optional CPU thread limits and feature-scoped dependency checks.
- Add pinned repair/download of the original Chinese ASR model; Japanese and notes-only transcription can run independently.
- Load task history asynchronously and protect custom task folders during cache cleanup.
- Fix cancellation leaving subprocesses running and concurrent JSON writes failing on Windows.
- Remove verified unused runtime files while preserving original model weights and component licenses.
- Preserve sub-10-tick note durations in native USTX exports, preventing artificial overlaps that stopped editor-project conversion.
- Surface Vocal2Midi 2.0 stage logs and live carriage-return progress updates instead of leaving the display at model loading.
- Avoid clipping compressed stem exports when float audio exceeds full scale; use shared peak attenuation, record the gain and preserve original WAV files.
- Preserve modified MIDI, project and encoded audio files in task backups before regenerating them; unchanged generated outputs do not create duplicates.


## 0.3.1 — 2026-10-11

### Fixed

- Use direct connections for Bilibili metadata, original covers and media downloads, independent of system and environment proxies.
- Apply the same direct connection policy to component downloads, program updates and runtime installation while retaining accelerated download sources.
- Use direct connections in the dedicated sign-in/upload browser.
- Clarify Bilibili connection failures and keep the existing task available for retry.

### Changed

- Check, download and apply program updates automatically at startup by default, displaying a ten-second restart countdown. Manual downloads require an explicit restart action.
- Postpone startup installation to the next launch once the user interacts with the assistant or an operation has begun; do not check periodically during use.
- Allow postponing installation or disabling automatic restart in Settings/updates.
- Save workflow preferences before applying an update and include the running version in startup diagnostics.

## 0.3.0 — 2026-10-11

- Add a draggable workflow canvas with validated execution connections, named presets and per-engine device selection.
- Let the processing page select saved workflows directly; synchronize preset choices and parameters across both pages.
- Replace two-click port wiring with proximity previews, snap-to-connect on drop and pull-apart disconnection.
- Expand task search, filtering, sorting, naming and deletion; show interrupted tasks and recorded settings.
- Show stage/subtask progress, elapsed time and download throughput; add actionable errors and CPU retry.
- Add asynchronous disk usage scans and confirmed cleanup of disposable caches and task temporary files.
- Add an offline LRC/TXT/CSV/MIDI lyric editor with SRT and WebVTT export and optional workflow subtitle output.
- Reduce the portable runtime inventory by 447 MB while retaining active model weights; default new full builds to ZIP level 6.

### Added

- A custom save directory for downloaded videos, audio and MIDI, with remembered task locations.
- Chinese MIDI lyric output in characters or pinyin, saved per task.
- Official and accelerated download routes with resume and SHA-256 verification.
- A Windows EXE launcher with an original application icon and no console window.
- Startup update checks, optional automatic downloads, and verified restart-to-install updates.
- Update rollback that preserves configuration, tasks, sign-in profiles and installed models.

### Changed

- Increased the default workspace to 1600 × 1000, with screen-size adaptation.
- Larger typography, translucent cards, gradient controls and animated hover highlights.
- Grouped diagnostic scripts in `tools/` and revised interface/help text.
- Revised English and Chinese documentation and release instructions.
- Set the Chinese application name to 术力口工作流助手.

## 0.2.0 — 2026-10-09

- Complete portable package with the redesigned workspace and optional BS-RoFormer installation.
- Retained Japanese ASR weights and alignment improvements.
- Simplified release attachments and portable runtime contents.

## 0.1.2 — 2026-10-08

- Japanese alignment fixes and MIDI import guidance.

## 0.1.1 — 2026-10-08

- Runtime installation for source checkouts.

## 0.1.0 — 2026-10-08

- Initial portable workflow release.
