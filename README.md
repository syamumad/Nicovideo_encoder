# ニコ動コメントプレイヤー

ローカルの動画ファイルにニコニコ動画のコメントを重ねて再生するデスクトップアプリです。  
JSON・XML 両形式のコメントファイルに対応し、コメント付きで動画を書き出す機能も備えています。

> ネット接続不要。手元のファイルだけで動作します。

---

## 目次

- [機能](#機能)
- [必要環境](#必要環境)
- [セットアップ](#セットアップ)
- [使い方](#使い方)
- [コメントファイルの対応形式](#コメントファイルの対応形式)
- [コマンドの対応状況](#コマンドの対応状況)
- [キー操作](#キー操作)
- [書き出し機能（コメント合成）](#書き出し機能コメント合成)
- [音声について](#音声について)
- [ファイル構成](#ファイル構成)
- [既知の制限](#既知の制限)

---

## 機能

- 🎬 **動画再生** — MP4 / WebM / MKV など Qt 対応形式
- 💬 **コメントオーバーレイ** — ニコ動そっくりのスクロール・固定・色・サイズ再現
- 📄 **JSON / XML 両対応** — nvapi 形式 JSON と ニコ動 XML エクスポートを自動判別
- 🎞️ **コメント付き書き出し** — FFmpeg を使って合成済み MP4 を出力（要 FFmpeg）
- 🔊 **音声デバイス切替** — Bluetooth イヤホン含む出力先を動的に変更
- ⚡ **軽量描画** — ピクスマップキャッシュで 30fps を維持

---

## 必要環境

| 項目 | 要件 |
|---|---|
| OS | Windows 10/11（macOS・Linux も動作する見込みですが未検証） |
| Python | 3.10 以上 |
| PySide6 | 6.5 以上（`requirements.txt` 参照） |
| FFmpeg | **書き出し機能のみ**必要。[ffmpeg.org](https://ffmpeg.org/download.html) からインストール |

---

## セットアップ

```powershell
# 依存ライブラリのインストール（PySide6 のみ）
python -m pip install -r requirements.txt
```

---

## 使い方

### 起動

```powershell
# GUI からファイルを選択して起動
python niconico_player.py

# 動画を指定（同名の .json / .xml があれば自動読み込み）
python niconico_player.py sm28492754.mp4

# 動画とコメントファイルを明示的に指定
python niconico_player.py sm28492754.mp4 sm28492754.json
python niconico_player.py video.mp4 comments.xml
```

### ファイルを開く

| 方法 | 説明 |
|---|---|
| ドラッグ＆ドロップ | 動画・JSON・XML をウィンドウへドロップ |
| ボタン | 「動画を開く」「JSONを開く」ボタン（JSON/XML 両方選択可） |
| 自動ペア読み込み | 動画を開いたとき、同フォルダに同名の `.json` → `.xml` があれば自動で読み込む |

---

## コメントファイルの対応形式

### JSON（2形式）

| 形式 | 例 |
|---|---|
| **nvapi スレッドAPI形式** | `data.threads[].comments[]` を持つ公式 API レスポンス |
| **配列 / オブジェクト形式** | `[{"vpos": 1.5, "body": "test", "commands": ["ue"]}]` など自作 JSON |

時刻キーは `vposMs`・`vpos_ms`・`vpos`・`time`・`start` に対応（秒またはミリ秒を自動判別）。

### XML

ニコニコ動画の XML エクスポート形式（`<d p="vpos,type,size,color,..." mail="コマンド">本文</d>`）に対応。

- `p` 属性の 10進 RGB カラー整数を `#RRGGBB` に自動変換
- `mail` 属性のコマンド文字列（`shita red big` など）を解釈

---

## コマンドの対応状況

| コマンド | 対応状況 |
|---|---|
| `ue` / `shita` / `naka` | ✅ 位置（上・下・中央）|
| `big` / `medium` / `small` | ✅ サイズ |
| 色名 (`red` `blue` `green` `yellow` `cyan` `purple` `orange` `pink` `black` `white`) | ✅ |
| `#RRGGBB` | ✅ カスタムカラー |
| `ender` / `patissier` | ✅ 90度回転 |
| `invisible` | ✅ 非表示 |
| `@vpos` | ✅ 投稿者コメントの時刻指定 |
| `full` / `device:*` / `184` / `@emoji` / `niconicoblack` | ⚠️ 無視（表示に影響なし） |

---

## キー操作

| キー | 動作 |
|---|---|
| `Space` | 再生 / 一時停止 |
| `←` / `→` | 5秒シーク |
| `Shift+←` / `Shift+→` | 1秒シーク |
| `↑` / `↓` | 音量 ±5% |
| `M` | 消音切替 |
| `F11` | 全画面（操作バーを隠す） |
| `Esc` | 全画面解除 |
| `Ctrl+O` | 動画を開く |
| `Ctrl+J` | コメントファイルを開く（JSON / XML） |
| `Ctrl+M` | コメント表示の切替 |
| `Ctrl+R` | 音声デバイスを再読み込み |
| `Ctrl+Shift+N` | 次のコメントへジャンプ |

---

## 書き出し機能（コメント合成）

動画とコメントを合成した MP4 ファイルとして書き出せます。

### 手順

1. FFmpeg をインストールして PATH に通す → [ffmpeg.org](https://ffmpeg.org/download.html)
2. 動画とコメントファイルを開く
3. 「**書き出し**」ボタンをクリック
4. 保存先を選択 → 合成開始（進捗バー表示、キャンセル可能）

### 仕組み

```
入力動画 ──→ FFmpeg デコード ──→ raw RGBA フレーム
                                        │
                               Qt でコメントを描画
                             （PixmapCache を再利用）
                                        │
                               FFmpeg エンコード
                             ＋ 元音声コピー
                                        │
                                出力 MP4（H.264/AAC）
```

> [!NOTE]
> 書き出し中は再生が一時停止します。長い動画や高解像度の場合は時間がかかります。

---

## 音声について

- 音量スライダー・消音ボタン・出力先ドロップダウンで制御できます
- Windows では接続済みの音声デバイスを自動検出します
- **Bluetooth イヤホンが一覧に出ないとき**: Bluetooth を接続済みにした後、**⟳**（`Ctrl+R`）で再読み込みしてください

---

## ファイル構成

```
niconico_player.py              起動スクリプト
requirements.txt                依存ライブラリ（PySide6）
niconico_comments/
    commands.py                 コマンド解釈（色・サイズ・位置・回転）
    comment_parser.py           JSON / XML 読み込み → Comment リスト
    layout.py                   レーン割り当て・スクロール位置計算（GUI非依存）
    overlay.py                  動画に重ねるコメント描画（PixmapCache付き）
    video_view.py               フレーム表示ウィジェット（アスペクト比維持）
    exporter.py                 コメント合成書き出し（FFmpeg連携）
    main_window.py              メインウィンドウ・操作パネル
tools/
    inspect_json.py             JSONの時刻分布を確認する補助スクリプト
```

---

## 既知の制限

- 対応動画コーデックは Qt（Windows は Media Foundation）依存です。**MP4(H.264/AAC) を推奨**します。再生不可の場合はエラーダイアログが表示されます
- コメントは 1 レーンあたり最大 3 件まで重ねます（それ以上は間引いて読みやすくします）
- `commentCount` より実コメント数が少ない場合（API のページ分けが必要な場合）、ステータスバーに警告を表示します
- 書き出し時に回転コメント（`ender` / `patissier`）は省略されます

---

## ライセンス

MIT
