<p align="center">
  <img src="assets/app.svg" width="84" alt="VWA アイコン">
</p>

<h1 align="center">VWA · Vocaloid Workflow Assistant</h1>

<p align="center">
  <strong>Windows 向けオールインワン歌声合成ワークフロー支援デスクトップアプリ</strong><br>
  楽曲準備 · 歌詞連動調声 · マルチトラック・コーラス修音 · 12 形式プロジェクト変換 · 16 トラック簡易ミックス · 動画合成＆投稿準備
</p>

<p align="center">
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest">
    <img src="https://img.shields.io/github/v/release/Transilent/Better-Vocaloid-Workflow-Assistant?label=Release&color=3b6ce8" alt="Latest Release">
  </a>
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/actions/workflows/ci.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/Transilent/Better-Vocaloid-Workflow-Assistant/ci.yml?branch=main&label=CI" alt="CI Status">
  </a>
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/actions/workflows/codeql.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/Transilent/Better-Vocaloid-Workflow-Assistant/codeql.yml?branch=main&label=CodeQL" alt="CodeQL">
  </a>
  <img src="https://img.shields.io/badge/Platform-Windows%2010%2F11%20x64-0078D6" alt="Windows x64">
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/Transilent/Better-Vocaloid-Workflow-Assistant?color=43c58e" alt="MIT License">
  </a>
  <img src="https://img.shields.io/badge/QQ%20Group-490619616-12B7F5" alt="QQ Group 490619616">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <strong>日本語</strong>
</p>

<p align="center">
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest"><strong>v3.0.0 をダウンロード</strong></a> ·
  <a href="docs/UserGuide.en.md">利用ガイド (EN)</a> ·
  <a href="docs/UserGuide.zh-CN.md">利用ガイド (中文)</a> ·
  <a href="CHANGELOG.md">変更履歴</a> ·
  <a href="https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose">不具合報告</a>
</p>

---

## v3.0.0 の主な新機能

- **マルチトラック・コーラス修音ワークベンチ（Chorus Workbench）**：複数のボーカルステムと参照 MIDI を比較しながらフレーズ単位でピッチ手描き・音節ダイナミクス調整を行い、Signalsmith Stretch ネイティブ高品質ピッチシフト（フォルマント保持）で連続試聴・書き出しできます。
- **12 形式の安全なプロジェクト相互変換（Project Converter）**：SVP / SV2、USTX、VPR、VSQX、ACEP、UFData、MIDI など 12 形式間の相互変換・トラック分割・読み込み整合性チェック・タイムライン付き LRC / SRT 歌詞書き出しに対応（元ファイルは一切上書きしません）。
- **16 トラック・ネイティブリアルタイムミックスエンジン**：WASAPI デバイス選択とホットプラグ自動再接続、ネイティブリミッター、60fps FBO 波形、下書き自動復旧に対応。
- **BS-RoFormer デュアルランタイム共存＆実験的 AMD WebGPU ONNX**：PyTorch CUDA / CPU の共存に加え、AMD などの GPU 向けに隔離環境で動作する実験コンポーネント **BS-RoFormer ONNX（`10s FP16` / `20s FP16`）**（`@AU-ki` 氏提供）を追加（既定オフ、重みは本リポジトリにミラー済み）。
- **単一ファイル・オンラインインストーラー＆2 段階アプリ内自動移行**：日本語 UI 対応の軽量単一ファイル `VWA-Online-Installer-3.0.0-x64.exe`（約 139 KB）を新設。旧版からのアプリ内 2 段階自動移行にも対応。
- **操作性・セキュリティ強化**：コマンドパレット（`Ctrl+K`）、ショートカット一覧（`Ctrl+/`）、ウィンドウへのドラッグ＆ドロップ自動振り分け、一括準備キュー、コンポーネント状態表示の改善、依存パッケージのセキュリティ更新を実施。

---

## 主なワークスペース

| ワークスペース | 内容 |
| --- | --- |
| **準備** | ローカル音声や Bilibili の BV 番号・リンクを読み込み（一括キュー対応）、メインボーカル・コーラス・伴奏を分離。日本語・英語・中国語の歌詞と音符を認識し、MIDI、USTX、VSQX、SVP を書き出します。 |
| **調声** | タスクやローカルの SVP / SV2（`version: 203+` 独立グループ対応）、USTX、VPR、VSQX、ACEP、UFData を開き、歌詞連動編集・ピッチ描画・参照歌唱マッピング・実測校正を行い、別ファイルに保存します。 |
| **コーラス修音** | 複数トラックの歌声ステムと参照 MIDI を使ったフレーズ単位のピッチ／ダイナミクス修音と高品質連続試聴・ステム書き出し。 |
| **プロジェクト変換** | 12 種類の歌声合成プロジェクト形式を元ファイル非破壊で相互変換し、トラック分割・検証・LRC / SRT 書き出しを行います。 |
| **かんたんミックス** | 最大 16 トラックのネイティブ連続 PCM ミキサー。音量・パン・ミュート／ソロ・タイミング補正・リバーブセンド・共有ボーカルバス EQ／ディエッサー／コンプレッサー／リミッター・A/B 試聴に対応。 |
| **動画合成＆投稿** | 元動画と Mixdown の自動位置合わせ・合成、サムネイル・タイトル・クレジット整理（Bilibili アップロード支援、小紅書向けローカル引き渡しパッケージ）。 |
| **モデル・部品＆設定** | Qwen3-ASR、BS-RoFormer（PyTorch CUDA / CPU・実験版 ONNX WebGPU）、Pedalboard の導入・SHA-256 検証、テーマ（ダーク／ライト）・表示言語（日本語／英語／簡体字中国語）・[プリセット管理](docs/PresetLibrary.en.md)。 |
| **Agent／MCP（任意）** | 互換ホスト向け 12 個の型付きトップレベルツールを提供する独立オプション（汎用コマンド実行機能は含みません）。 |

---

## ダウンロードとインストール

[**VWA v3.0.0 リリースページ**](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest)から用途に合わせて選択してください。全パッケージで日本語・英語・簡体字中国語の UI を切り替えられます。

| 導入方法 | おすすめ対象 | ダウンロードファイル | 特徴 |
| --- | --- | --- | --- |
| **⭐ オンラインインストーラー（推奨）** | 新規導入＆旧版からの非破壊上書き更新 | `VWA-Online-Installer-3.0.0-x64.exe`（約 139 KB） | 日本語 UI・ダーク／ライト対応の単一実行ファイル。画面上で **JPN 軽量版 / Full 完全版** と保存先を選び、最速ミラー自動計測・マルチスレッド再開・SHA-256 検証で自動展開します。 |
| **ポータブル版 · JPN 軽量版** | 日本語楽曲中心・すぐ試したい方 | `VWA-Windows-x64-JPN.zip`（約 1.55 GiB・単一 ZIP） | 中国語 Qwen3-ASR 重みのみ省略した単一アーカイブ。日本語認識・全ワークベンチがすぐ使え、必要時に「モデル・部品」から中英モデルを追加できます。 |
| **ポータブル版 · Full 完全版** | オフライン保管・全モデル同梱 | `VWA-Windows-x64.zip.001`～`.003` + `portable-metadata-full.json` + `Merge-PortableParts.ps1`（結合後約 4.93 GiB） | 中国語／英語 Qwen3-ASR を含む完全構成。 |

> **ご注意**：GitHub 自動生成の **Source code** および更新用ブリッジ ZIP（`VWA-Program-Update.zip`、`VWA-App-Update-*.zip`、`VWA-Runtime-Upgrade-*.zip`、`BVWA-App-Update.zip`）は単体では起動できません。

### ポータブル版 Full の結合手順

`VWA-Windows-x64.zip.001`、`.002`、`.003`、`portable-metadata-full.json`、`Merge-PortableParts.ps1` を同じフォルダーに保存し、PowerShell で次を実行します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Merge-PortableParts.ps1
```

生成された `VWA-Windows-x64.zip`（または単一の `VWA-Windows-x64-JPN.zip`）を短い書き込み可能なパス（例：`D:\Apps`）に展開し、`VWA` フォルダー内の **`VWA.exe`** を起動してください。各ファイルは `SHA256SUMS.txt` で検証できます。

---

## 旧バージョンからのアップグレード

| 現在のバージョン | アップグレード手順 | データの保持 |
| --- | --- | --- |
| **`v2.0.0` / `v2.1.0`** | 旧版の**設定・サポート → 更新を確認**から再起動すると、ローカル Web UI 移行ツールが起動し 3.0.0 を自動ダウンロード・移行します。 | `config.json`、`jobs/`、`user-presets/`、`cache/accounts/`、`publish-packages/`、`dependencies/optional/`、追加済み Qwen 重みを自動移行。旧フォルダーはそのまま残ります。 |
| **`v1.0.0` / `v1.1.0`** | 旧版の**設定・更新 → 更新を確認**から 2 段階ブリッジ経由で 3.0.0 移行ツールが起動します。 | 同上（設定・タスク・モデルを非破壊で移行）。 |
| **`v0.4.0`** | アプリ内更新からの 2 段階移行、またはオンラインインストーラーに対応。 | 同上。 |
| **`v0.3.x` / `v0.4.1`** | **`VWA-Online-Installer-3.0.0-x64.exe`** を起動し、既存フォルダーを指定して非破壊上書き更新してください。 | 既存の設定・タスク・プリセット・追加モデルを保持します。 |

---

## 動作環境と GPU サポート

- **対応 OS**：Windows 10 / 11 x64（Python および Qt 6.10.3 ランタイム同梱）。歌声合成には別途対応エディターと歌声ライブラリが必要です。
- **NVIDIA GPU**：基本 DirectML ＋ オプション **BS-RoFormer（PyTorch CUDA）** を推奨。
- **AMD / Intel GPU・内蔵 GPU**：基本 DirectML ＋ オプション **BS-RoFormer ONNX（実験版 WebGPU）**（VRAM 8 GB 以下は `10s FP16`、12 GB 以上は `20s FP16` を推奨）。
- **CPU 自動フォールバック**：DirectML デバイスリセット発生時などは自動的に CPU へ切り替わります。

---

## ドキュメントとお問い合わせ

- **ユーザーコミュニティ**：QQ グループ **`490619616`**
- **不具合報告・要望**：[GitHub Issues](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose)（日本語・英語・中国語に対応）
- **関連ドキュメント**：[利用ガイド (EN)](docs/UserGuide.en.md) · [利用ガイド (中文)](docs/UserGuide.zh-CN.md) · [調声ガイド (EN)](docs/TuningWorkflow.en.md) · [ポータブル版ガイド (EN)](docs/Portable.en.md) · [更新プロトコル (EN)](docs/UpdateProtocol.en.md) · [変更履歴](CHANGELOG.md)

---

## 謝辞とライセンス

以下のオープンソースプロジェクト・コントリビューターの皆様に感謝いたします。

| プロジェクト / コントリビューター | VWA での用途 |
| --- | --- |
| [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi) | 音符認識と歌詞アラインメント。 |
| [LibreSVIP](https://github.com/SoulMelody/LibreSVIP) | 12 形式の歌声合成プロジェクト読み書き・変換。 |
| [Signalsmith Stretch](https://github.com/Signalsmith-Audio/signalsmith-stretch) | コーラス修音ワークベンチの高品質連続ピッチシフト・フォルマント保持。 |
| [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui)／[UVR モデル](https://github.com/TRvlvr/model_repo) | 歌声分離・モデル重み。 |
| [Pymss](https://github.com/pymss-project/pymss)／[becruily BS-RoFormer karaoke](https://huggingface.co/becruily/bs-roformer-karaoke) | 追加の分離エンジンとモデル。 |
| [@AU-ki](https://github.com/AU-ki) | BS-RoFormer ONNX エクスポートツール（`tools/export_roformer_onnx.py`）および各種 GPU での WebGPU 検証。 |
| [Pymss Studio](https://github.com/pymss-project/pymss-studio) | UI・モデル管理設計参考。 |
| [PyWORLD](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder)／[WORLD](https://github.com/mmorise/World) | 参照歌唱の音響分析。 |
| [163MusicLyrics](https://github.com/jitwxs/163MusicLyrics) / [yt-dlp](https://github.com/yt-dlp/yt-dlp) / [FFmpeg](https://ffmpeg.org/) / [Playwright](https://github.com/microsoft/playwright-python) | 歌詞検索・メディア取得・音動画処理・投稿支援。 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime)／[PyTorch](https://pytorch.org/)／[Qt Quick・PySide6](https://doc.qt.io/qtforpython-6.10/) | 推論ランタイム・デスクトップ UI。 |
| [NumPy](https://numpy.org/)、[SciPy](https://scipy.org/)、[librosa](https://librosa.org/)、[SoundFile](https://github.com/bastibe/python-soundfile)、[Mido](https://github.com/mido/mido)、[Pillow](https://python-pillow.org/) | 数値・音声・MIDI・画像処理。 |

組み込まれた Vocal2Midi 処理には [GAME](https://github.com/openvpi/GAME)、[HubertFA](https://github.com/wolfgitpr/HubertFA)、[LyricFA](https://github.com/wolfgitpr/LyricFA)、[RomajiASR](https://github.com/Xiantaidu/RomajiASR)、[llama.cpp](https://github.com/ggml-org/llama.cpp) も使用されています（詳細は[上流プロジェクトの謝辞](dependencies/vocal2midi2/ACKNOWLEDGEMENTS.md)を参照）。

- **ライセンス**：VWA 独自のコードは [MIT ライセンス](LICENSE)です。第三者コード・ランタイム・モデルのライセンスは[第三者に関する通知](THIRD_PARTY_NOTICES.md)および [v3.0.0 ランタイム通知](docs/ThirdPartyRuntime.md)をご確認ください。
