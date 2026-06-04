# 音声文字起こし → 議事録 パイプライン（Gemini 連携）

Notion に保存された録音（ogg 等）を Gemini で文字起こしし、全文を **Notion ＋ Google
ドキュメント**へ保存、議事録を生成して md / Notion へ同期するための一式。

設計の肝は **音声バイナリと全文テキストを Claude のコンテキストに通さない**こと。
取得・変換・保存はすべてコンテナ内のスクリプトが直接 API を叩くので、Claude のトークン
消費は「Craft の短いプロンプト」と「短い議事録」だけで済む。

## 構成

| 段階 | スクリプト / 手段 | コンテキスト通過 |
|---|---|---|
| ① ogg をディスクに取得 | `notion_fetch_audio.py`（NOTION_TOKEN） | ❌ |
| ①' Notion ページ本文をディスクに取得 | `notion_fetch_text.py`（NOTION_TOKEN） | ❌ |
| ② 文字起こし | `gemini_transcribe.py`（GEMINI_API_KEY）→ `> transcript.txt` | ❌ |
| ③ 議事録生成 | `gemini_transcribe.py`（議事録プロンプト）→ `> minutes.md` | ❌ |
| ④ 全文を Notion へ | `notion_append_text.py`（NOTION_TOKEN） | ❌ |
| ⑤ 全文を Google ドキュメントへ | `gdoc_upload.py`（サービスアカウント） | ❌ |
| ⑥ 議事録を md / Notion へ同期 | Claude が `minutes.md` を読んで反映 | ⭕（短い） |

プロンプトは Craft が正本。Claude が `craft_read` で読み、`GEMINI_PROMPT` 環境変数 or
引数でスクリプトに渡す。`gemini_transcribe.py` は入力が音声でもテキストでも扱える
（テキスト入力＝議事録生成などに利用）。

### プロンプト参照先（Craft `00_PromptLibrary`）

| 用途 | 場所 | 既定/選び方 |
|---|---|---|
| 文字起こし | `00_CommonPrompts` →「共通プロンプト」コレクション `E6ED28C3-4C8B-4A0B-BC85-40A5F78672C8` | 既定 = item `0E6D825E`（汎用・使用中Yes）。説明会は `DF5C04F8` |
| 議事録生成 | 録音内容で使い分け | 打合せ構造化 `A4BB82E0`（共通・使用中Yes）／候補者面談分析 `68588E5F`（02_Interview）／説明会 `69E617FE`（05_ClientMeeting） |

> 実行時に該当コレクションの「使用中=Yes」を `craft_read` で読み、`GEMINI_PROMPT` で渡す
> （ID 固定埋め込みはしない＝Craft 更新が自動反映）。
> 全文の保存先は **Notion ＋ Google ドキュメント**（Craft には保存しない）。

## 必要な環境変数

| 変数 | 用途 |
|---|---|
| `GEMINI_API_KEY` | Gemini API（Google AI Studio で発行） |
| `GEMINI_MODEL` | 任意。既定 `gemini-2.5-flash` |
| `NOTION_TOKEN` | Notion インテグレーショントークン（対象ページに**共有**しておく） |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | サービスアカウント鍵。ファイルパス or JSON 文字列 |
| `GDRIVE_FOLDER_ID` | 任意。Doc 作成先フォルダ/共有ドライブ ID |
| `GOOGLE_IMPERSONATE_SUBJECT` | 任意。ドメイン全体委任で代理するユーザー |

> 環境変数は**セッション（コンテナ）起動時に注入**される。後から追加した場合は新しい
> セッションを開始すると反映される。

## サービスアカウント準備手順（Google ドキュメント保存用）

1. Google Cloud でプロジェクトを用意し、**Drive API** を有効化。
2. サービスアカウントを作成し、JSON 鍵を発行。
3. その JSON を環境変数 `GOOGLE_SERVICE_ACCOUNT_JSON` に登録（パス or 中身）。
4. 保存先の扱いを決める（いずれか）:
   - **(推奨) 共有ドライブ**を作り、サービスアカウントのメール（`...@....iam.gserviceaccount.com`）
     をメンバー追加。`GDRIVE_FOLDER_ID` に共有ドライブ/フォルダ ID を設定。
   - もしくは佐藤の Drive のフォルダをサービスアカウントに共有し、その ID を設定。
     （サービスアカウントは個人 Drive に容量を持たないため、共有ドライブが堅牢）
   - 佐藤本人名義で作成したい場合は**ドメイン全体委任**を設定し
     `GOOGLE_IMPERSONATE_SUBJECT=sato-y@pnl.co.jp` を指定。

## 実行例（全体）

```bash
# ① 取得（ページ配下の添付を /tmp に落とし、パスを得る）
AUDIO=$(NOTION_TOKEN=$NOTION_TOKEN python3 tools/notion_fetch_audio.py <page_id> /tmp | tail -1)

# ② 文字起こし（全文はディスクへ。Claude のコンテキストに出さない）
GEMINI_PROMPT="$(cat /tmp/transcribe_prompt.txt)" \
  python3 tools/gemini_transcribe.py "$AUDIO" > /tmp/transcript.txt

# ③ 議事録生成（文字起こし全文を入力、議事録プロンプトで処理）
GEMINI_PROMPT="$(cat /tmp/minutes_prompt.txt)" \
  python3 tools/gemini_transcribe.py /tmp/transcript.txt > /tmp/minutes.md  # ※音声以外の入力対応は要拡張

# ④ 全文を Notion へ
python3 tools/notion_append_text.py <page_id> /tmp/transcript.txt "文字起こし全文"

# ⑤ 全文を Google ドキュメントへ
python3 tools/gdoc_upload.py /tmp/transcript.txt "2026-05-31 文字起こし"

# ⑥ minutes.md を Claude が読んで適切な md / Notion に同期
```

> 注: `gemini_transcribe.py` はテキスト入力にも対応済み（③ の議事録生成）。文字起こしが
> 既に Notion ページにある場合（例：文字起こし DB に保存済みの 1on1）は、音声取得①・文字
> 起こし②を飛ばし、`notion_fetch_text.py` でページ本文をディスクに落として ③ に渡せる。
>
> ```bash
> # 既存の文字起こしページ → 議事録（音声不要）
> python3 tools/notion_fetch_text.py <文字起こしページID> > /tmp/transcript.txt
> GEMINI_PROMPT="$(cat /tmp/minutes_prompt.txt)" \
>   python3 tools/gemini_transcribe.py /tmp/transcript.txt > /tmp/minutes.md
> ```
