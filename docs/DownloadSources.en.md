# Download sources

[简体中文](DownloadSources.zh-CN.md)

Select the item to install in **Models & components**, then choose **Download source**. Each component remembers its own selection. **Settings & support → Downloads and storage** controls the app update source separately. The download connection limit is shared.

| Component | Available sources |
| --- | --- |
| Built-in Karaoke 2 and Japanese recognition | Included with the application. Restore the matching installation files if they are missing. |
| BS‑RoFormer (CPU / CUDA) | **ModelScope weights + GitHub config** or **GitHub Release (weights and config)**. TUNA, USTC, and Aliyun PyPI serve the Python dependencies. SJTU serves the pinned CPU or CUDA PyTorch wheel. |
| Chinese lyric recognition (Qwen3‑ASR) | Official Hugging Face and HF‑Mirror. HF‑Mirror may redirect to Hugging Face. No independent source has passed verification. |
| Reference vocal timbre (WORLD) | Official PyPI, TUNA, USTC, and Aliyun PyPI. |
| Pedalboard (command-line installation) | Official PyPI, TUNA, USTC, and Aliyun PyPI. The graphical interface can still install from a local wheel. |

**Automatic** tries official addresses first and falls back to matching alternatives. **Official direct** uses only official addresses. A named provider changes the order for the files it serves; other files use their own providers. If Hugging Face is unreachable, select either BS‑RoFormer alternative. Neither option uses Hugging Face. ModelScope serves the weights only; the config comes from the pinned GitHub release asset or its verified backup route. If you need Chinese recognition without access to Hugging Face, the standard VWA package includes the Qwen weights.

Every route uses the same file versions, sizes and SHA‑256 values. Downloads can be cancelled and resumed. Files are enabled only after verification. To report a failure, include the component, runtime (CPU or CUDA), selected source and the full error from its card. The error names the file, hosts and error codes; a full configuration or model is not needed. Normal refresh in **Chinese lyric recognition** checks presence and size; run the full check separately from the component card.

App updates still support Automatic, Official direct, Prefer acceleration, GitProxy, GH‑Proxy and ghfast.top. Third-party routes apply only to public files. Sign-in, upload and signed requests retain their original services.

The BS‑RoFormer and Pedalboard installers support `--download-source`. New IDs are `modelscope` and `github-release` for BS‑RoFormer, and `pypi-ustc` for PyPI dependencies. The existing `mirror` command-line mode is still accepted.
