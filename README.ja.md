<p align="center"><img src="assets/app.svg" width="80" alt="VWA アイコン"></p>

# VWA · Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

VWA は、楽曲の準備、歌詞に沿った調声、歌声のミックス、動画合成、投稿用資料の作成を支援する Windows 向けアプリです。

**ユーザーコミュニティ：QQ グループ 490619616。** 使い方や問題、ご意見の共有にご利用ください。

[不具合・機能提案・使い方の質問](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose) · [貢献ガイド（English）](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/blob/main/CONTRIBUTING.md)

**[VWA 2.0.0 をダウンロード（Windows x64）](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/tag/v2.0.0)** · [利用ガイド（中文）](docs/UserGuide.zh-CN.md)

2.0.0 では Qt Quick の作業画面を採用しました。調声は単一トラックと複数トラックを切り替えられ、ミックスは曲全体を再生しながら調整できます。初回ガイドはスキップでき、起動時の更新通知は後で表示するよう設定できます。

## 主な機能

| ワークフロー | 内容 |
| --- | --- |
| 準備 | ローカル音声や Bilibili の BV 番号・リンクを読み込み、メインボーカル・コーラス・伴奏を分離します。中国語・日本語の歌詞と音符を認識し、MIDI、USTX、VSQX、SVP を出力します。 |
| 調声 | タスクのプロジェクトや SVP・USTX・VPR を開き、歌詞を見ながら区間ごとにスタイルやピッチを調整し、同じ形式で別のファイルに保存します。 |
| かんたんミックス | 最大 3 本のレンダリング済み歌声 WAV と 1 本の伴奏を、トラック音量、パン、ミュート／ソロ、歌声のタイミング、リバーブセンド、共有ボーカルバスの EQ／コンプレッサー／リバーブで整え、編集可能な A/B 試聴を行えます。 |
| 動画合成 | 動画と Mixdown を組み合わせます。自動位置合わせ、手動オフセット、任意のラウドネス調整に対応します。 |
| 投稿 | サムネイル、タイトル、説明文とクレジットを整理します。Bilibili はアップロードを支援し、小紅書は手動投稿用の素材と文案を作成します。 |
| Agent／MCP（任意） | 対応ホストから VWA の 12 個の型付きトップレベルツールを利用し、準備、根拠の確認、判断、書き出し、復旧、納品を行えます。セットアップはデスクトップアプリとは独立しています。 |

調声では、元の表現の保持、参照歌唱からのマッピング、実測による歌声ライブラリの校正、既存パラメータの引き継ぎ、時間セグメント別スタイル、スタイルの自動保存、独立した中国戯曲唱法の実験レイヤーを選べます。参照マッピングと「やわらか」「悲しみ」「明るく軽快」の 3 種類の実験スタイルは編集可能な下書きです。対象の歌声ライブラリで合成し、試聴してください。実測校正には、現在の楽譜と歌声設定に対応する試聴用音声をユーザーが SV から書き出す必要があります。歌声の合成は対応するエディターで行います。

[かんたんミックス](docs/SimpleMixingComponent.en.md)は単一のネイティブ連続 PCM 試聴を使用し、ページ切り替えや最小化でも同じ再生セッションを維持します。メーター、EQ スペクトル、ゲインリダクション推定値、リバーブ表示には実際の試聴データを使います。デバイス障害時は一時停止し、明示的な再生操作で再試行します。対象の歌声ライブラリと音声デバイスで曲全体を試聴してください。元ファイルは保持されます。

タスク履歴と再開、処理プリセット、追加モデルのインストール、SRT・WebVTT 字幕の出力にも対応します。表示言語は簡体字中国語・日本語・英語から選択でき、ライト／ダークテーマと表示倍率を変更できます。MCP 技術プレビューは任意の別個インストールで、汎用コマンド実行機能を追加せず、MCP を使わなくてもデスクトップアプリを利用できます。

## ダウンロードとインストール

[2.0.0 リリース](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/tag/v2.0.0)の **Assets** から完全版または JPN 版を選んでください。

| パッケージ | 用途と内容 |
| --- | --- |
| 完全版 | 新規ユーザーと任意の旧バージョンのユーザーを含む全ユーザー向けのクリーンインストール経路です。完全なモデル一式と基本ランタイムを含みます。 |
| JPN 版 | ワークフローは完全版と同じで、中国語 Qwen の重みファイルだけを省略します。日本語認識と音符抽出を利用でき、中国語モデルは後から追加できます。 |
| 更新版 | 正式版の **1.0.0 と 1.1.0** が対象です。旧版の設定画面から更新します。 |

どちらの完全版も 3 言語の表示に対応します。表示言語と歌詞の認識言語は別々に設定します。GitHub の **Source code** アーカイブと更新用 ZIP だけではアプリを利用できません。

**完全版（約 5.41 GB）：** `VWA-Windows-x64.zip.001`、`.002`、`.003`、`portable-metadata-full.json`、`Merge-PortableParts.ps1` を同じフォルダーに保存し、そこで PowerShell を開いて次を実行します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Merge-PortableParts.ps1
```

スクリプトが 3 つの分割ファイルを検証して `VWA-Windows-x64.zip` を作成します。**JPN 版（約 1.74 GB）** は `VWA-Windows-x64-JPN.zip` を直接展開できます。書き込み可能な短いパス（例：`D:\Apps`）に展開し、`VWA` フォルダー内の **`VWA.exe`** を起動してください。

対応環境は Windows 10／11 x64 です。ポータブル版にはランタイムが含まれます。最終的なダウンロード・結合・展開に必要な容量は正式なリリースノートで確認し、追加コンポーネントや素材用の容量も確保してください。歌声合成には、対応するエディターと歌声ライブラリを用意してください。

## 使い方

1. **準備**で楽曲、歌詞の言語、処理方法、保存先を選びます。処理後に音声、音符、歌詞を確認します。
2. **調声**でタスクのプロジェクトを開くか、Synthesizer V Studio 1／2 の SVP、OpenUtau の USTX、VOCALOID 5／6 の VPR を読み込みます。歌詞から調整箇所を探し、新しいプロジェクトとして保存して元のエディターで試聴します。
3. 歌声のステムを書き出したら、**かんたんミックス**で最大 3 本の歌声と 1 本の伴奏を整え、試聴後に新しい WAV を書き出すか、タスクの Mixdown として明示的に登録します。
4. **動画を合成**で元動画と Mixdown を選びます。完成済みの動画は、そのまま**投稿**に進めます。
5. サムネイル、各サイトの下書き、クレジットを確認してアップロードを準備します。最後に投稿ページで内容を確認し、手動で公開します。

**タスクと結果**から処理の検索・続行・再試行ができます。追加モデルは**モデル・部品**から導入します。設定画面では表示言語の変更、初回ガイドの再表示、[ローカルプリセット](docs/PresetLibrary.en.md)の管理ができます。

## アップグレード

**正式版の 1.0.0／1.1.0：** 旧版の**設定・更新**で更新を確認し、案内に従って再起動します。完全版・JPN 版ともに設定、タスク、プリセット、ログイン状態、追加済みモデルとコンポーネントを保持します。JPN 版では未導入の中国語モデルを自動で追加しません。

**その他のバージョン：** 完全版または JPN 版を新しいフォルダーに展開してください。更新用 ZIP を手動で上書きせず、編集中のプロジェクトは事前にバックアップしてください。

## ドキュメントとお問い合わせ

[利用ガイド（中文）](docs/UserGuide.zh-CN.md) · [調声ガイド（English）](docs/TuningWorkflow.en.md) · [ポータブル版ガイド（English）](docs/Portable.en.md) · [MCP ツールリファレンス（中文）](docs/AgentMCPStandard.zh-CN.md) · [変更履歴](CHANGELOG.md) · [不具合の報告](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose)

## 謝辞

以下のプロジェクトの作者・コントリビューターの皆様に感謝します。

| プロジェクト | VWA での用途 |
| --- | --- |
| [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi) | 音符認識と歌詞のアラインメント。 |
| [LibreSVIP](https://github.com/SoulMelody/LibreSVIP) | USTX、VSQX、SVP の形式変換。 |
| [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui)／[UVR モデル](https://github.com/TRvlvr/model_repo) | 歌声分離、モデルの重みと設定情報。 |
| [Pymss](https://github.com/pymss-project/pymss)／[becruily BS-RoFormer karaoke](https://huggingface.co/becruily/bs-roformer-karaoke) | 追加の分離エンジンとモデル。 |
| [Pymss Studio](https://github.com/pymss-project/pymss-studio) | UI とモデル管理の設計参考。 |
| [PyWORLD](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder)／[WORLD](https://github.com/mmorise/World) | 参照歌唱の音響分析。 |
| [163MusicLyrics](https://github.com/jitwxs/163MusicLyrics) | 歌詞検索機能の参考。 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | メディアのダウンロード。 |
| [FFmpeg](https://ffmpeg.org/)／[Jellyfin FFmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) | 音声・動画処理。 |
| [Playwright for Python](https://github.com/microsoft/playwright-python) | ブラウザーでのログインとアップロード支援。 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime)／[PyTorch](https://pytorch.org/) | モデルの実行。 |
| [Qt Quick と PySide6](https://doc.qt.io/qtforpython-6.10/) | デスクトップ UI。 |
| [NumPy](https://numpy.org/)、[SciPy](https://scipy.org/)、[librosa](https://librosa.org/)、[SoundFile](https://github.com/bastibe/python-soundfile)、[Mido](https://github.com/mido/mido)、[Pillow](https://python-pillow.org/) | 数値、音声、MIDI、画像の処理。 |

組み込まれた Vocal2Midi の処理には、[GAME](https://github.com/openvpi/GAME)、[HubertFA](https://github.com/wolfgitpr/HubertFA)、[LyricFA](https://github.com/wolfgitpr/LyricFA)、[RomajiASR](https://github.com/Xiantaidu/RomajiASR)、[llama.cpp](https://github.com/ggml-org/llama.cpp)、関連する音声・ピッチモデルも使用されています。その他のクレジットは、同梱の[上流プロジェクトの謝辞](dependencies/vocal2midi2/ACKNOWLEDGEMENTS.md)をご覧ください。

## ライセンス

VWA 独自のコードは [MIT ライセンス](LICENSE)で提供します。第三者のコード、ランタイム、モデルの重みには、それぞれのライセンスと通知が適用されます。[第三者に関する通知（English）](THIRD_PARTY_NOTICES.md)をご確認ください。

[2.0.0 ランタイムのライセンスと対応ソース（English／中文）](docs/ThirdPartyRuntime.md)。
