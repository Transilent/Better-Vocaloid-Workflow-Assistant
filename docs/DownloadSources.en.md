# Download sources

[简体中文](DownloadSources.zh-CN.md)

Choose a route in **Settings & updates → Download settings**, or in **Models & components** before an installation. The setting is retained when an update restarts into the migration window.

- **Automatic** tries the official origin first, followed by supported alternatives.
- **Direct** uses the original service.
- **Prefer acceleration** selects alternative routes for each supported file origin, retaining direct fallback.
- **Named mirrors** prefer that provider for its matching origin, then fall back to direct. Files from other origins remain direct. For example, choosing TUNA PyPI does not route a Qwen model through TUNA.

## Available providers

| Provider | Files routed through it |
| --- | --- |
| GitProxy | Public GitHub release, raw and archive files |
| GH-Proxy | Public GitHub release, raw and archive files |
| ghfast.top | Public GitHub release, raw and archive files |
| HF-Mirror | Public Hugging Face `resolve` files, including the original Chinese Qwen weights |
| TUNA PyPI | Pinned wheels from `files.pythonhosted.org/packages/…` |
| Aliyun PyPI | Pinned wheels from `files.pythonhosted.org/packages/…` |
| SJTU PyTorch | The currently pinned Windows Python 3.12 torch 2.7.1 CPU and cu128 wheels |

GitHub acceleration is limited to explicit public file paths. API metadata, platform sign-in, upload requests, Bilibili signed media and NetEase requests use the original services. Cookies, tokens and signed query strings are not added to third-party accelerator requests. GitHub uploads remain direct.

PyPI mirrors use the same pinned package file, not a newly resolved package version. SJTU routing is limited to the two supported wheel filenames; other PyTorch versions and channels retain their original URLs. The PyPI mirrors do not proxy arbitrary package indexes or API requests.

## Verification, cancellation and recovery

Component and update installation uses fixed byte counts and SHA-256 values. A mirror does not select which model or library version is installed. Downloads retain existing range/resume, cancellation and fallback behavior. The small compatibility runtime installer also accepts a download-source option; it does not provide all of the desktop downloader's parallel/range features.

Open **Models & components → Chinese lyric recognition** to download Qwen when the weights are not bundled. The card shows size, retained partial downloads, progress and cancellation. Once files are present, **Verify / Repair** performs the full cancellable integrity check and repairs missing or damaged files. Normal page refresh checks file presence and size for responsiveness; it is not a new full SHA-256 verification. Japanese recognition and notes-only extraction do not require the Chinese weights.

Automatic updates run only at startup. Installing an update waits for active workflows, installation, cache operations and publication sessions to finish. Final publication remains a user action on the platform website.

## Command-line compatibility

The shared source IDs are `auto`, `direct`, `mirror`, `gitproxy`, `gh-proxy`, `ghfast`, `hf-mirror`, `pypi-tuna`, `pypi-aliyun`, and `pytorch-sjtu`.

The optional-component command accepts `--download-source <ID>`. The runtime recovery script accepts `-DownloadSource <ID>`. New migration helpers accept all ten IDs; a component-only mirror selected for a GitHub update falls back to direct rather than failing validation.
