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
| Desktop UI | Input selection, job state, publication controls |
| Local music | WAV/MP3/FLAC/M4A, snapshots, resume, invalid inputs, no source API requests |
| Browser lifecycle | Sign-in profiles, process handling, upload state |
| Platform adapters | File selection, text fields, cover controls on local pages |
| Dual covers | Independent 4:3 and 16:9 cover handling |
| Launchers | Single startup entry, runtime check, CPU/GPU settings |
| Runtime bootstrap | Offline install, Release-part downloads on local HTTP fixtures, integrity, retry and preserved user data |

## Release verification

The portable ZIP is extracted into a separate directory. Every member's CRC, size and SHA-256 are checked against the release inventory; fixed dependencies are cross-checked with their manifest. Startup and regressions are then run with the extracted Python. Split-part hashes and the full ZIP hash are recorded in `release-assets.json`.

Model verification also exercises real local separation and transcription, including two-part MIDI and lyrics. Repository tests use short fixtures; they do not assert musical accuracy for arbitrary songs. Release verification reports omit account state, private media, and machine-specific installation paths.

## Practical limits

- A verified archive confirms file integrity; other hardware, drivers and security software still need a local startup check.
- Separation can leak vocals or accompaniment. Backing transcription may contain few or no notes when the signal is weak.
- Automatic lyrics and pitches need human review. Reference lyrics can help alignment but do not guarantee correctness.
- Browser adapter tests verify local page behavior. Live platform compatibility can change, and each new computer needs fresh sign-in.
- The assistant prepares creator uploads. It does not verify all platform fields or publish automatically.
