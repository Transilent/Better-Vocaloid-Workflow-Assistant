# Third-party notices

[English](THIRD_PARTY_NOTICES.md) | [简体中文](THIRD_PARTY_NOTICES.zh-CN.md) | [README](README.md)

The portable package includes third-party components and model weights. Their original licenses and notices apply to those components; the name Better Vocaloid Workflow Assistant does not replace their ownership or license terms. No project-wide license has been selected for the assistant's original code.

| Component | Source / notice |
| --- | --- |
| Vocal2Midi | [Xiantaidu/Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi), Apache-2.0; included `dependencies/vocal2midi/LICENSE` and `ACKNOWLEDGEMENTS.md` |
| MDX separation model weights | [UVR model repository](https://github.com/TRvlvr/model_repo/releases/tag/all_public_uvr_models), [model parameter registry](https://github.com/TRvlvr/application_data); model licenses are distinct from the assistant code |
| Optional BS-RoFormer model | Original checkpoint/config from [becruily/bs-roformer-karaoke](https://huggingface.co/becruily/bs-roformer-karaoke); revision and hashes recorded in `components/roformer.json`; model terms remain separate |
| Optional Pymss, Pymss Core, PyTorch and supporting libraries | Downloaded from pinned upstream wheels; original package metadata and bundled notices are retained in `dependencies/optional/bs-roformer/current/packages` |
| Python | Python 3.12.10; included `dependencies/vocal2midi/python/LICENSE.txt` |
| Python libraries | Original `LICENSE`, `COPYING`, `.dist-info/licenses/` and package metadata retained in the portable runtime |
| yt-dlp | Included source, license and metadata in `vendor/`; [upstream](https://github.com/yt-dlp/yt-dlp) |
| Playwright and Node.js driver | Included upstream licenses and notices in `vendor/`; [upstream](https://github.com/microsoft/playwright-python) |
| FFmpeg | 7.1.3-Jellyfin, built with `--enable-gpl --enable-version3`; [Jellyfin FFmpeg source](https://github.com/jellyfin/jellyfin-ffmpeg), [FFmpeg licensing](https://ffmpeg.org/legal.html) |
| Google Chrome | Browser runtime; [Chrome terms](https://www.google.com/chrome/terms/), open-source component notices available through `chrome://credits` |

The original model directories and bundled libraries retain upstream materials. GAME, HubertFA, Qwen3-ASR, RMVPE, RomajiASR, llama.cpp and ONNX Runtime acknowledgements are included with Vocal2Midi. This snapshot was tested as one fixed environment; it does not imply that licenses for all third-party binaries are identical.

The sidebar/card layout draws design inspiration from [Pymss Studio](https://github.com/pymss-project/pymss-studio). The assistant's Qt layout and SVG controls are original; Studio's frontend code and assets are not bundled.

The base archive excludes the optional BS-RoFormer payload as well as browser profiles, cookies, saved sessions, source songs, videos, covers, generated MIDI, and user publication drafts. Third-party redistribution terms apply to their corresponding components in the binary package.
