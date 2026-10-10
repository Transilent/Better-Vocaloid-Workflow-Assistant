# Changelog

## Unreleased

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
