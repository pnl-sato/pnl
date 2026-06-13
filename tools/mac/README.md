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
| `imessage_sync.py` | 候補者との iMessage/SMS だけを chat.db（読取専用）から抜き、候補者1人=1ページにまとめて Notion へ。候補者DBで許可リスト・差分追記・常駐可 | `python3 tools/mac/imessage_sync.py [--dry-run\|--probe\|--setup]` | 標準ライブラリのみ（要 FDA） |
| `com.pnl.imessage-sync.plist` | 上を **launchd で15分ごとに定期実行**する定義 | `~/Library/LaunchAgents/` に置いて `launchctl load` | launchd |
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

## iMessage/SMS 履歴を Notion へ同期（`imessage_sync.py`）

候補者との iMessage/SMS のやりとり「だけ」を、常時起動の mac mini 上の chat.db から
**読み取り専用**で抜き出し、Notion に保存する。許可リストの正本は**本来の「候補者」DB**で、
その候補者ページの `携帯番号` に一致する相手のやりとりだけを取り込む（家族・私用など許可リスト外の
番号は構造上いっさい外に出ない＝デフォルト拒否）。別建ての「番号マスター」は作らない。

保存形式は **「候補者1人 = メッセージ履歴ページ1枚」**。会話ログをそのページ本文に時系列で追記して
いく（行＝候補者数だけで最軽量、Claude Code が候補者1ページ fetch で全履歴を網羅的に読める）。
メッセージ履歴ページは候補者ページへ `候補者` relation でひも付く。差分は watermark（最後に処理した
message.ROWID）で管理するので本文への二重追記は起きない。Web/スマホ版 Claude Code からも普段の
Notion コネクタで履歴を読め、候補者ページからその人との履歴を辿れる。

```
[候補者DB: 携帯番号] ─(許可リスト)─┐
[iPhone] --iCloud/SMS転送--> [mac mini: chat.db] --(該当番号だけ抽出)--> [Notion: メッセージ履歴DB]
                                                                          └候補者1人=1ページ(本文に会話ログ)・候補者へ relation
                                                                              ▲ Web/スマホ版 Claude Code が1ページで網羅読み
```

### セットアップ（mini で1回ずつ）

1. **フルディスクアクセス（必須）**：Claude Code を動かすアプリ（ターミナル.app か VSCode）を
   「システム設定 → プライバシーとセキュリティ → フルディスクアクセス」に追加。これが無いと
   `~/Library/Messages/chat.db` を開けない。
2. **SMS も取るなら**：iPhone の「設定 → メッセージ → テキストメッセージ転送」で mini をオン。
3. **メッセージ履歴 DB を作る**（候補者 DB に relation した状態で作成）：
   ```bash
   NOTION_TOKEN=ntn_xxx python3 tools/mac/imessage_sync.py --setup \
       --parent <親ページのpage_id> --candidate-db <候補者DBの id>
   ```
   出力された `IMESSAGE_MESSAGES_DB_ID` / `IMESSAGE_CANDIDATE_DB_ID` を `tools/mac/.env` に設定する。
   Notion インテグレーションに候補者 DB とメッセージ履歴 DB が共有されているか確認する。
4. **候補者 DB の各候補者ページに `携帯番号` を入れる**（許可リストの正本。これが一致条件）。
   表記は `080-xxxx-xxxx` でも `+81…` でも内部で正規化される。

### 運用

```bash
python3 tools/mac/imessage_sync.py --probe     # chat.db のハンドルと候補者DBの一致状況（誰の番号か）を表示（本文は出さない）
python3 tools/mac/imessage_sync.py --dry-run   # 誰の何件が取り込まれるかだけ表示（Notion 書込なし）
python3 tools/mac/imessage_sync.py             # 差分のみ Notion へ upsert（GUID で重複防止・ROWID で差分管理）
```

常駐させる場合は `com.pnl.imessage-sync.plist`（15分ごと）を `~/Library/LaunchAgents/` に置いて
`launchctl load`。メッセージ DB は `相手(title) / 候補者(relation→候補者DB) / 候補者ID / 最終更新` 構成で、
1メッセージ＝ページ本文の1段落（`時刻 ▶送信/◀受信：本文`）として追記される。

> **PII の扱い**：許可リスト（候補者ページの携帯番号）に一致したやりとりだけが Notion に複製される。
> 業務外（家族・私用）は出ない設計だが、候補者の個人メッセージは Notion に乗るため、メッセージ履歴 DB は
> 外部共有しないプライベート扱いに徹すること。読取専用で chat.db には一切書き込まない。

## ⚠ マシン固有・要調整（2台のMacで違う場合は各自で）

これらのスクリプトには**ハードコードされたパス**が残っている。`satouyuuta`／`/pnl` が両Macで同じなら基本そのままで動くが、違う場合は以下を直す。

- `com.pnl.video-processor.plist` … `ProgramArguments` とログの絶対パス（`/Users/<user>/pnl/tools/mac/...`）。**`scripts/` → `tools/mac/` へ移設済みなので、旧版を入れている場合は要差し替え。**
- `mac_ogg_watcher.sh` … 監視先 `WATCH_DIR="/Users/satouyuuta/Desktop/00_Download_sync"`。
- `start_video_processor.sh` … venv はリポジトリ直下 `venv/` 前提、監視先は `~/Desktop`。
- `video_processor.py` は `.env` を**スクリプトと同じ場所（`tools/mac/.env`）から読む**（`load_dotenv(__file__ の隣)`）。`imessage_sync.py` も同じ場所の `.env` を読む（依存なしの最小ローダ）。
- `com.pnl.imessage-sync.plist` … `ProgramArguments` とログの絶対パス（`/Users/<user>/pnl/...`）をこの Mac に合わせる。`imessage_sync.py` は標準ライブラリのみなので venv 不要・システム `python3` で動く。

## .env で使う主なキー（`.env.example` 参照）

`GEMINI_API_KEY` / `GEMINI_MODEL` / `NOTION_TOKEN` / `NOTION_MEMO_DB_ID` / `TRANSCRIPT_PROMPT_DIR` / `MINUTES_PROMPT_DIR` / `YOUTUBE_CLIENT_SECRETS_FILE` / `YOUTUBE_PRIVACY` / `VIDEO_CRF` / `AUDIO_BITRATE` / `PROCESSED_LOG`。

## 出自（来歴）

集約元の作業ブランチ（2026-04 頃）。重複していた `video_processor.py` は新しい方（video-processing-automation 版）を採用。

- `video-processing-automation`：video_processor 一式・テンプレ・常駐 plist
- `convert-ogg-audio-files`：convert_ogg ＋ 自動変換セットアップ
- `fix-mono-audio-playback`：fix_mono_audio

> 文字起こしの**サーバ側パイプライン**（Notion録音→Gemini→Notion/GoogleDoc）は別物で、`tools/`（`gemini_transcribe.py` 等）＋ `agents/transcription.md` が正本。こちらは Web/ローカル問わず動く。`tools/mac/` は**ローカルMacの入口（録画・録音の取り込み変換）**という役割分担。
