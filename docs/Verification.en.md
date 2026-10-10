# Verification and limits

[English](Verification.en.md) | [简体中文](Verification.zh-CN.md) | [README](../README.md)

## Reproducible checks

Run from the extracted application folder:

```powershell
& '.\dependencies\vocal2midi\python\python.exe' -B .\launch.py --check
& '.\dependencies\vocal2midi\python\python.exe' -B .\check_dependencies.py --full
& '.\dependencies\vocal2midi\python\python.exe' -B .\tests\run_tests.py
```

The first checks startup requirements. The second hashes every fixed dependency. Regression tests run offscreen with generated fixtures and local test pages; they do not publish a post or require an account.

| Test group | Coverage |
| --- | --- |
| Core | Stage reuse, cancellation, stem normalization, MIDI checks |
| Japanese MIDI | Short mora pauses, kana token positions, supported phones, chunk error isolation, cancellation and atomic track assembly |
| Chinese MIDI | Real lyric-token selection and MIDI writing in characters/pinyin with a recognition fixture; unchanged note timing and cache invalidation |
| Program updates | Version checks, download routes, staging, file allowlist, hash rejection, preserved data and rollback |
| Desktop UI | Eight-page navigation, folded controls, primary actions, component state, multi-GB progress signals, default 1600×1000 and minimum 1000×680 layouts |
| Workflow and task management | Real input snapshots/FFmpeg and imported stems, graph-controlled termination, inference fixture and real subtitle output, validated resume, processing-page preset selection and actual start, Qt proximity snapping and disconnection, task search, cache boundaries and retry |
| Subtitles | Repeated/empty/enhanced LRC timestamps, offset tags, SRT/WebVTT time formatting, text estimates, Unicode MIDI and tempo maps |
| Optional installer | Local HTTP Range resume, ignored/bad Range responses, SHA-256 mismatch, cancellation, archive traversal and cleanup boundaries |
| Publication workspace | Real synthetic-video bundle preparation, primary action dispatch, edited-draft guard, custom-cover restore and error recovery |
| Custom save location | Real synthetic video/WAV outputs, Unicode/spaces, resume/reuse, remembered task folders, browsing/restart and invalid destinations |
| Local music | WAV/MP3/FLAC/M4A, snapshots, resume, invalid inputs, no source API requests |
| Browser lifecycle | Sign-in profiles, process handling, upload state |
| Platform adapters | File selection, text fields, cover controls on local pages |
| Dual covers | Independent 4:3 and 16:9 cover handling |
| Launchers | Actual Windows GUI-subsystem EXE startup without a console, runtime check and CPU/GPU settings |
| Runtime bootstrap | Offline install, Release-part downloads on local HTTP fixtures, integrity, retry and preserved user data |

## Release verification

Before delivery packaging, `tools/verify_portable_layout.py` creates a temporary test tree from the curated inventories and runs the regression suite using its own Python. Excluded dependencies are physically absent. Large immutable assets use NTFS hard links; writable files are copied. The test tree is removed afterward, and no delivery archive is created. A short real MDX probe additionally checks CPU and DirectML execution, timeline length, stem reconstruction and structured chunk progress. Neither fixture-based inference nor short tone probes measure lyric or music accuracy.

The portable ZIP is extracted into a separate directory. Every member's CRC, size and SHA-256 are checked against the release inventory; fixed dependencies are cross-checked with their manifest. Startup and regressions are then run with the extracted Python. Split-part hashes and the full ZIP hash are recorded in `release-assets.json`.

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
