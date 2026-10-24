<p align="center"><img src="assets/app.svg" width="80" alt="VWA アイコン"></p>

# VWA · Vocaloid Workflow Assistant

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

VWA は、楽曲の準備、SVP の調声、動画合成、投稿用資料の作成を支援する Windows 向けデスクトップアプリです。

## 主な機能

| ワークフロー | 内容 |
| --- | --- |
| 準備 | ローカル音声や Bilibili の BV 番号・リンクを読み込み、メインボーカル・コーラス・伴奏を分離します。中国語・日本語の歌詞と音符を認識し、MIDI、USTX、VSQX、SVP を出力します。 |
| 調声 | 既存タスクのプロジェクトや SVP を開き、対象トラックとパラメータの取得方法を選びます。歌唱スタイルや局所的なピッチ表現を加え、別のプロジェクトとして保存します。 |
| 動画合成 | 動画と Mixdown を組み合わせます。自動位置合わせ、手動オフセット、任意のラウドネス調整に対応します。 |
| 投稿 | サムネイル、タイトル、説明文、クレジットを整理し、Bilibili・小紅書へのアップロードを支援します。公開は各サイト上で確認して実行します。 |

調声では、元の表現の保持、参照歌唱からのマッピング、実測による歌声ライブラリの校正、既存パラメータの引き継ぎを選べます。参照マッピングやスタイルプリセットの出力は編集可能な下書きです。対象の歌声ライブラリで試聴してください。実測校正には、現在の楽譜と歌声設定に対応する試聴用音声をユーザーが SV から書き出す必要があります。歌声の合成とミキシングは、対応するエディターや音声ソフトで行います。

タスク履歴と再開、処理プリセット、追加モデルのインストール、SRT・WebVTT 字幕の出力にも対応します。表示言語は簡体字中国語・日本語・英語から選択でき、ライト／ダークテーマと表示倍率を変更できます。

## ダウンロードとインストール

[Releases](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/tag/v1.0.0) から **1.0.0** をダウンロードしてください。

| パッケージ | ダウンロード容量 | 内容 |
| --- | ---: | --- |
| 完全版 | 約 4.91 GiB、3 分割 | 中国語・日本語の認識モデルと基本ランタイムを含みます。 |
| [JPN 版](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/download/v1.0.0/VWA-Windows-x64-JPN.zip) | 約 1.53 GiB | 中国語 Qwen モデルの重みファイル 3 個のみを省略しています。日本語認識と音符抽出を利用でき、中国語モデルは後から追加できます。 |

どちらも 3 言語の表示に対応します。表示言語と歌詞の認識言語は別々に設定します。GitHub の **Source code** アーカイブと更新用 ZIP だけではアプリを利用できません。

**完全版：**次の 5 ファイルを同じフォルダーに保存してください。

- `VWA-Windows-x64.zip.001`、`.002`、`.003`
- `portable-metadata-full.json`
- `Merge-PortableParts.ps1`

そのフォルダーで PowerShell を開き、次を実行します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Merge-PortableParts.ps1
```

スクリプトが各分割ファイルを検証し、`VWA-Windows-x64.zip` を作成します。この ZIP を完全に展開してください。JPN 版は、ダウンロードした ZIP をそのまま展開します。展開後の `VWA` フォルダーにある **`VWA.exe`** を起動します。移動する場合はフォルダー全体を保持してください。

対応環境は Windows 10／11 x64 です。ポータブル版にはランタイムが含まれます。完全版のダウンロード・結合・展開には約 25 GB の空き容量を推奨します。追加コンポーネントや素材には別途容量が必要です。歌声合成には、対応するエディターと歌声ライブラリを用意してください。

## 使い方

1. **準備**で楽曲、歌詞の言語、処理方法、保存先を選びます。処理後に音声、音符、歌詞を確認します。
2. **調声**でタスクのプロジェクトか SVP を開き、対象トラック、パラメータの取得方法、任意のスタイルを選びます。別保存したプロジェクトを SV で試聴します。
3. 歌声の合成とミキシングを終えたら、**動画を合成**で元動画と Mixdown を選びます。完成済みの動画は、そのまま**投稿**に進めます。
4. サムネイル、各サイトの下書き、クレジットを確認してアップロードを準備します。最後に投稿ページで内容を確認し、手動で公開します。

**タスクと結果**からタスクの検索・続行・再試行を行えます。追加モデルは**モデル・部品**からインストールします。表示言語は**設定・更新**の外観設定で変更でき、VWA を再起動すると反映されます。

## アップグレード

| 現在のバージョン | 方法 |
| --- | --- |
| 0.4.0／0.4.1 | **設定・更新**に表示される手順に従います。 |
| 0.3.0／0.3.1 | [VWA-Updater.exe](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/download/v1.0.0/VWA-Updater.exe) を実行し、案内に従います。 |

対応するアップグレードでは、設定、タスク、ログイン状態、インストール済みモデルと追加コンポーネントを保持します。編集中のプロジェクトは事前にバックアップしてください。配布ページの `BVWA` 名のファイルは、旧クライアントとの互換性のために提供しています。

## ドキュメントとお問い合わせ

[利用ガイド（中文）](docs/UserGuide.zh-CN.md) · [調声ガイド（English）](docs/TuningWorkflow.en.md) · [変更履歴](CHANGELOG.md) · [不具合の報告](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues)

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
| [PyQt](https://www.riverbankcomputing.com/software/pyqt/) | デスクトップ UI。 |
| [NumPy](https://numpy.org/)、[SciPy](https://scipy.org/)、[librosa](https://librosa.org/)、[SoundFile](https://github.com/bastibe/python-soundfile)、[Mido](https://github.com/mido/mido)、[Pillow](https://python-pillow.org/) | 数値、音声、MIDI、画像の処理。 |

組み込まれた Vocal2Midi の処理には、[GAME](https://github.com/openvpi/GAME)、[HubertFA](https://github.com/wolfgitpr/HubertFA)、[LyricFA](https://github.com/wolfgitpr/LyricFA)、[RomajiASR](https://github.com/Xiantaidu/RomajiASR)、[llama.cpp](https://github.com/ggml-org/llama.cpp)、関連する音声・ピッチモデルも使用されています。その他のクレジットは、同梱の[上流プロジェクトの謝辞](dependencies/vocal2midi2/ACKNOWLEDGEMENTS.md)をご覧ください。

## ライセンス

VWA 独自のコードは [MIT ライセンス](LICENSE)で提供します。第三者のコード、ランタイム、モデルの重みには、それぞれのライセンスと通知が適用されます。[第三者に関する通知（English）](THIRD_PARTY_NOTICES.md)をご確認ください。
