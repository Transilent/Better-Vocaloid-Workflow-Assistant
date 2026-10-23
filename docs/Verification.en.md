# Verification and limits

[English](Verification.en.md) | [简体中文](Verification.zh-CN.md) | [README](../README.md)

## Reproducible checks

Check startup and installed dependencies from the application folder:

```powershell
& '.\dependencies\vocal2midi\python\python.exe' -B .\launch.py --check
& '.\dependencies\vocal2midi\python\python.exe' -B .\check_dependencies.py --full
```

For regression tests, use a source checkout with its runtime installed:

```powershell
& '.\tools\Prepare-Source.ps1'
& '.\dependencies\vocal2midi\python\python.exe' -B .\tests\run_tests.py
```

The runner rejects stale generated program files, then creates a disposable application copy. Each test subprocess starts with default settings and fresh task/cache directories. Installed user settings, recent tasks, output preferences, sign-in profiles and documentation images are excluded. Editable dependency files are copied; immutable binary payloads share NTFS storage. Tests run offscreen with generated media and local pages, without publishing or requiring an account. The temporary application is removed afterward. Run the suite through this entry point rather than invoking individual UI scripts against a working installation.

| Test group | Coverage |
| --- | --- |
| Core | Stage reuse, cancellation, stem normalization, MIDI checks |
| Audit regressions | Disabled export choices preserved through preset save/reopen and task restore; empty audio exports and CLI; verified original-cover reuse and offline publication preparation; MIDI-only RMVPE preflight; measured subtitle note releases, melisma and unrelated tails |
| Japanese MIDI | Short mora pauses, kana token positions, supported phones, chunk error isolation, cancellation and atomic track assembly |
| Chinese MIDI | Real lyric-token selection and MIDI writing in characters/pinyin with a recognition fixture; unchanged note timing and cache invalidation |
| Test isolation | Fresh per-test state, abrupt failure/exit cleanup, preserved installed files and no account-state imports |
| Program updates | Version checks, download routes, staging, file allowlist, hash rejection, preserved data, rollback, same-plan retries and malformed-plan failure receipts |
| VWA update compatibility | Pinned, unmodified 0.3.0/0.4.0/0.4.1 update code; runtime-hash routing; frozen three-file first hop; canonical `VWA-Updater.exe`; preservation of models and private user files; direct/mirror/auto routing; real GUI startup |
| Desktop UI | Five-page navigation, folded controls, input recovery, active-task display, component state, large-file progress, default 1600×1000 and minimum 1000×680 layouts |
| Workflow and task management | Real input snapshots/FFmpeg and imported stems, graph-controlled termination, inference fixture and real subtitle output, validated resume, processing-page preset selection and actual start, Qt proximity snapping and disconnection, task search, cache boundaries and retry |
| Subtitles | Repeated/empty/enhanced LRC timestamps, offset tags, SRT/WebVTT time formatting, rejection of untimed text, Unicode MIDI and tempo maps |
| Optional installer | Local HTTP Range resume, ignored/bad Range responses, SHA-256 mismatch, cancellation, archive traversal and cleanup boundaries |
| Publication workspace | Real synthetic-video bundle preparation, primary action dispatch, edited-draft guard, custom-cover restore and error recovery |
| Custom save location | Real synthetic video/WAV outputs, Unicode/spaces, resume/reuse, remembered task folders, browsing/restart and invalid destinations |
| Local music | WAV/MP3/FLAC/M4A, snapshots, resume, invalid inputs, no source API requests |
| Browser lifecycle | Sign-in profiles, process handling, upload state |
| Platform adapters | File selection, text fields, cover controls on local pages |
| Dual covers | Independent 4:3 and 16:9 cover handling |
| Launchers | Canonical `VWA.exe` GUI-subsystem startup without a console, dual-lock ownership and stale-lock recovery, and the hidden `BVWA.exe` alias only for upgraded installations |
| Runtime bootstrap | Offline install, Release-part downloads on local HTTP fixtures, integrity, retry and preserved user data |

## Release verification

An old client's file allowlist cannot be expanded by adding new files to the same download. `packaging/update-clients.json` pins original clients and their source hashes. The direct-update builder rejects files they cannot install. The [update compatibility protocol](UpdateProtocol.en.md) provides verified installer bootstraps, runtime-specific deltas, and the canonical `VWA-Updater.exe` path for remaining released clients. Current clients stage both parts before a single restart; unmodified 0.4.0/0.4.1 clients retain their migration-window path. The regression uses synthetic release metadata and small dependency fixtures, runs original client code, and starts the no-console `VWA-Updater.exe`. It does not download a new public release or measure model accuracy. Building delivery archives still requires user testing confirmation.

Before delivery packaging, `tools/verify_portable_layout.py` creates a temporary test tree from the curated inventories and runs the regression suite using its own Python. Excluded dependencies are physically absent. Large immutable assets use NTFS hard links; writable files are copied. The test tree is removed afterward, and no delivery archive is created. A short real MDX probe additionally checks CPU and DirectML execution, timeline length, stem reconstruction and structured chunk progress. Neither fixture-based inference nor short tone probes measure lyric or music accuracy.

The portable ZIP is extracted into a separate directory. Every member's CRC, size and SHA-256 are checked against the release inventory; fixed dependencies are cross-checked with their manifest. Startup and regressions are then run with the extracted Python. Canonical split-part and full-ZIP hashes are recorded in the matching `portable-metadata-full.json` or `portable-metadata-jpn.json`. The top-level `release-assets.json` `archive` and `parts` fields remain legacy BVWA-root compatibility metadata for the frozen `Install-Runtime.ps1` path.

Model verification also exercises real local separation and transcription, including two-part MIDI and lyrics. Repository tests use short fixtures; they do not assert musical accuracy for arbitrary songs. Release verification reports omit account state, private media, and machine-specific installation paths.

The optional-component integration was additionally checked using real CPU and
CUDA downloads and original-model loading. Short Chinese and Japanese samples
exercise the offscreen Start action, CUDA separation, and note-only two-part MIDI.
This verifies integration and output structure, not lyric or musical accuracy.
The retained ASR paths are covered by the existing transcription regression tests.

## Practical limits

- A verified archive confirms file integrity; other hardware, drivers and security software still need a local startup check.
- Separation can leak vocals or accompaniment. Backing transcription may contain few or no notes when the signal is weak.
- Automatic lyrics and pitches need human review. Reference lyrics can help alignment but do not guarantee correctness.
- Browser adapter tests verify local page behavior. Live platform compatibility can change, and each new computer needs fresh sign-in.
- The assistant prepares creator uploads. It does not verify all platform fields or publish automatically.

## Task contracts

`test_task_contract.py` covers a real local-audio/imported-stem pipeline, file modifications, producer-receipt migration, partial voice and format commits, failed-stage states, human-file revisions, index recovery, cross-task cleanup protection, path rejection and concurrent worker commits. `test_task_contract_ui.py` exercises the actual dialog offscreen, task navigation and publication state with real FFmpeg media. Project-export regression checks that cached formats remain valid after a new export attempt. These checks validate file and state handling; they do not measure musical accuracy or certify live creator-platform behavior.
