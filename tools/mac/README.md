# tools/mac/ — Mac ローカル・ツール群（音声・動画変換／録画処理）

佐藤の **Mac（mini／MacBook 共通）でローカル実行する**スクリプト一式。録音・録画の変換、文字起こし常駐、音声補正など、macOS 依存（ffmpeg・AppleScript 通知・launchd 常駐・ローカルフォルダ監視）の処理をまとめる。

> **実行できるのは「そのMac上で動く Claude Code（VSCode/CLI）」だけ。** これらは BlackHole・AppleScript・launchd・ローカルの Desktop/ダウンロードフォルダに依存するため、**Web版 Claude Code（クラウド実行）からは読取・編集はできても実行はできない。** 2台の Mac はどちらも `main` を pull すれば同じ最新版を共有できる。

## ツール一覧

| スクリプト | 役割 | 実行 | 主依存 |
|---|---|---|---|
| `video_processor.py` | BlackHole録画(.mov)を監視→ffmpegでmp4/m4a→YouTubeアップロード＋Gemini文字起こしを並列実行。常駐運用可 | `python3 tools/mac/video_processor.py watch ~/Movies/Recordings` | ffmpeg, google-genai, YouTube API |
| `process_now.sh` | 単発で手動処理するラッパー（Finder クイックアクション「動画処理」用）。iCloud dataless の実体化待ち付き。パスはスクリプト位置から自動解決（両Mac共通） | Finder で選択→クイックアクション、または `tools/mac/process_now.sh <file>` | zsh, ffmpeg |
| `com.pnl.video-processor.plist` / `start_video_processor.sh` | 上を **launchd で常駐**（ログイン時自動起動）するための定義と起動ラッパー | `~/Library/LaunchAgents/` に置いて `launchctl load` | launchd |
| `convert_ogg.py` | OGG→MP3/M4A 変換（NotebookLM取り込み用） | `python3 tools/mac/convert_ogg.py input.ogg [-f m4a] [-o out/]` | pydub, ffmpeg |
| `mac_ogg_watcher.sh` / `setup_mac_auto_convert.sh` | ダウンロードフォルダを監視し OGG を自動 MP3 変換（launchd常駐をセットアップ） | `bash tools/mac/setup_mac_auto_convert.sh` | fswatch, ffmpeg |
| `fix_mono_audio.py` | BlackHole録音が片耳になる問題を補正（モノラル→ステレオ） | `python3 tools/mac/fix_mono_audio.py input.wav` | 標準ライブラリのみ |
| `minutes_templates/` `prompt_templates/` | 面談種別（候補者面談・打ち合わせ・説明会・面接同席）別の議事録／文字起こしプロンプト雛形 | video_processor から参照 | — |

## セットアップ（各 Mac で1回）

```bash
# 1. リポジトリ直下で venv を作成し依存を入れる
cd <repo>            # 例: ~/pnl
python3 -m venv venv && source venv/bin/activate
pip install -r tools/mac/requirements.txt

# 2. Homebrew のツール
brew install ffmpeg fswatch

# 3. 環境変数（このディレクトリの .env として作成。git管理外）
cp tools/mac/.env.example tools/mac/.env
#   GEMINI_API_KEY / NOTION_TOKEN / NOTION_MEMO_DB_ID / YOUTUBE_CLIENT_SECRETS_FILE 等を設定

# 4-a. OGG自動変換を常駐させる
bash tools/mac/setup_mac_auto_convert.sh

# 4-b. 動画処理を常駐させる（plist のパスをこのMacに合わせてから）
cp tools/mac/com.pnl.video-processor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pnl.video-processor.plist
```

## ⚠ マシン固有・要調整（2台のMacで違う場合は各自で）

これらのスクリプトには**ハードコードされたパス**が残っている。`satouyuuta`／`/pnl` が両Macで同じなら基本そのままで動くが、違う場合は以下を直す。

- `com.pnl.video-processor.plist` … `ProgramArguments` とログの絶対パス（`/Users/<user>/pnl/tools/mac/...`）。**`scripts/` → `tools/mac/` へ移設済みなので、旧版を入れている場合は要差し替え。**
- `mac_ogg_watcher.sh` … 監視先 `WATCH_DIR="/Users/satouyuuta/Desktop/00_Download_sync"`。
- `start_video_processor.sh` … venv はリポジトリ直下 `venv/` 前提、監視先は `~/Desktop`。
- `video_processor.py` は `.env` を**スクリプトと同じ場所（`tools/mac/.env`）から読む**（`load_dotenv(__file__ の隣)`）。

## .env で使う主なキー（`.env.example` 参照）

`GEMINI_API_KEY` / `GEMINI_MODEL` / `NOTION_TOKEN` / `NOTION_MEMO_DB_ID` / `TRANSCRIPT_PROMPT_DIR` / `MINUTES_PROMPT_DIR` / `YOUTUBE_CLIENT_SECRETS_FILE` / `YOUTUBE_PRIVACY` / `VIDEO_CRF` / `AUDIO_BITRATE` / `PROCESSED_LOG`。

## 出自（来歴）

集約元の作業ブランチ（2026-04 頃）。重複していた `video_processor.py` は新しい方（video-processing-automation 版）を採用。

- `video-processing-automation`：video_processor 一式・テンプレ・常駐 plist
- `convert-ogg-audio-files`：convert_ogg ＋ 自動変換セットアップ
- `fix-mono-audio-playback`：fix_mono_audio

> 文字起こしの**サーバ側パイプライン**（Notion録音→Gemini→Notion/GoogleDoc）は別物で、`tools/`（`gemini_transcribe.py` 等）＋ `agents/transcription.md` が正本。こちらは Web/ローカル問わず動く。`tools/mac/` は**ローカルMacの入口（録画・録音の取り込み変換）**という役割分担。
