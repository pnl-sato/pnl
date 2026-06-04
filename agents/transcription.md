# transcription.md — 音声文字起こし → 議事録 パイプライン（Gemini 連携）

Notion `AppSoundcore` 等に保存された録音（ogg/m4a 等）や面談・打ち合わせ音声を、Gemini で
文字起こしし、全文を **Notion ＋ Google ドキュメント**に保存、議事録を生成して md / Notion へ
同期するための手順。実体スクリプトは `tools/`、技術詳細は `tools/README.md` を参照。

## 0. 設計の肝（コスト原則）

**音声バイナリと全文テキストを Claude のコンテキストに通さない。** 取得・変換・保存は
すべてコンテナ内のスクリプトが直接 API を叩く（curl／urllib）。Claude が文脈に載せるのは
「Craft の短いプロンプト」と「短い議事録」だけ。これによりトークン消費をほぼ0に抑える。

- ❌ やってはいけない：Drive MCP の `download_file_content` で音声を取得（base64 が文脈に流入＝高コスト）
- ✅ 正しい：Notion API（署名URL）→ S3 から `tools/notion_fetch_audio.py` でディスクへ直接DL
- ✅ 全文の Notion 追記・Google ドキュメント保存もスクリプトが直接 API へ（`> file` でディスク経由）

## 1. 前提（環境変数・ネットワーク）

| 変数 | 用途 |
|---|---|
| `GEMINI_API_KEY` | Gemini API |
| `GEMINI_MODEL` | 任意。既定 `gemini-2.5-flash` |
| `NOTION_TOKEN` | Notion インテグレーション。対象ページに**共有（接続）**しておくこと |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | サービスアカウント鍵（base64 推奨／JSON文字列／ファイルパスも可） |
| `GDRIVE_FOLDER_ID` | Doc 保存先＝**共有ドライブ** `0APHNtdofxu4CUk9PVA`（SAをメンバー追加済） |

ネットワークは許可リスト方式。`api.notion.com` / `generativelanguage.googleapis.com` /
`oauth2.googleapis.com` / `www.googleapis.com` / `prod-files-secure.s3...` が許可済み。
許可外なら `x-deny-reason: host_not_allowed` が返る（その場合は環境のネットワークポリシーに追加→新セッション）。

> サービスアカウントは自前のドライブ容量を持たないため、Doc は必ず**共有ドライブ**に作る
> （マイドライブのフォルダ共有では `storageQuotaExceeded` になる）。

## 2. プロンプトの正本（Craft `00_PromptLibrary`）

ID 固定で埋め込まず、**実行時に該当コレクションの「使用中=Yes」を `craft_read` で読み、
`GEMINI_PROMPT` で渡す**（Craft 更新が自動反映される）。

- **文字起こし**：`00_CommonPrompts` →「共通プロンプト」コレクション
  `E6ED28C3-4C8B-4A0B-BC85-40A5F78672C8`
  - 既定 = item `0E6D825E`（汎用：打ち合わせ／候補者面談、逐語・話者ラベル・整形最小）
  - 採用説明会向け = `DF5C04F8`
- **議事録（録音内容で使い分け）**
  - 打ち合わせ構造化 = `A4BB82E0`（共通コレクション、使用中Yes。概要/アジェンダ/決定事項/Action/懸念/ToDo）
  - 候補者面談 分析レポート = `68588E5F`（`02_Interview`）
  - 採用説明会 議事録 = `69E617FE`（`05_ClientMeeting`）

## 3. 実行手順

> **文字起こしが既に Notion にある場合（音声不要）**：1on1 の文字起こし DB など、
> テキストが既にページに保存済みなら、3.1 録音取得・3.2 文字起こしは飛ばし、
> `tools/notion_fetch_text.py <ページID> > /tmp/transcript.txt` で本文をディスクに
> 落として 3.3 議事録生成へ直行する（全文はコンテキストに通さない）。

### 3.1 録音の取得（Notion → ディスク）
`AppSoundcore`（page `33b7d017b6a081f59306cdc965fa6fab`）配下の各子ページに ogg が1つ添付。
対象の**子ページID**（または block ID）を渡す。
```bash
AUDIO=$(python3 tools/notion_fetch_audio.py <子ページID> /tmp | tail -1)
```

### 3.2 文字起こし（音声 → /tmp/transcript.txt）
Craft から文字起こしプロンプト（既定 `0E6D825E`）を読み、渡す。
```bash
GEMINI_PROMPT="<Craftの文字起こしプロンプト本文>" \
  python3 tools/gemini_transcribe.py "$AUDIO" > /tmp/transcript.txt
```

### 3.3 議事録生成（テキスト入力 → /tmp/minutes.md）
録音内容に合う議事録プロンプトを選び、文字起こし txt を入力に渡す
（`gemini_transcribe.py` はテキスト入力も対応）。
```bash
GEMINI_PROMPT="<Craftの議事録プロンプト本文>" \
  python3 tools/gemini_transcribe.py /tmp/transcript.txt > /tmp/minutes.md
```

### 3.4 全文の保存（コンテキストを通さない）
```bash
# Notion（対象ページに追記）
python3 tools/notion_append_text.py <保存先ページID> /tmp/transcript.txt "文字起こし全文"
# Google ドキュメント（共有ドライブ）
python3 tools/gdoc_upload.py /tmp/transcript.txt "<タイトル>"   # GDRIVE_FOLDER_ID を使用
```

### 3.5 議事録の同期
`/tmp/minutes.md` は短いので Claude が読み、内容に応じて適切な正本へ反映する：
- 1on1／方針 → Notion 1on1 DB（`CLAUDE.md` セクション9）
- 候補者面談 → 候補者プロファイル md（Craft、セクション7）
- クライアント打ち合わせ → クライアント／ポジション md（Craft、セクション8）
- ToDo が出たら Notion ToDo DB（セクション10）へ `Inbox 📨` で起票

## 4. 留意点

- 機微情報（候補者面談など）は Gemini（Google）に送信される。NotebookLM 利用時と同等の前提。
- Notion の rich_text は 1ブロック2000字・1回100ブロック制限 → `notion_append_text.py` が自動分割。
- 音声 20MB 以上は Gemini Files API 経由（スクリプトが自動切替）。
- サービスアカウント鍵をチャットに貼った場合は、後で必ずローテーション（再発行）する。
- 日付の扱いは毎回 `Today's date` を再参照（`CLAUDE.md` セクション6）。
