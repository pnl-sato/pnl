# tools/ ユーティリティ

このディレクトリには、Claude のコンテキストを節約しつつ正本データを直接 API から
取得・更新するためのスクリプトを置く。

## ToDo の未完一覧（残タスクの正本取得）｜`notion_active_todos.py`

**残タスク・やることの確認は、必ずこのスクリプトを使う。** `notion-search`（件名の
セマンティック検索）で ToDo を拾うと**ステータスが見えず完了済みが混ざり**、完了タスクを
「残タスク」として誤提示する事故が起きる（2026-06 発生）。本スクリプトは Notion API を
**ステータス≠完了でフィルタ**して、本当に未完のものだけを決定的に返す。

```bash
# 未完すべて（Category混在。Private・マンション理事会等も含む）
python3 tools/notion_active_todos.py

# 朝の業務一覧（P&L 案件のみ・推奨）
python3 tools/notion_active_todos.py --category "Pole&Line"

# いま動いているものだけ
python3 tools/notion_active_todos.py --status "進行中"

# 次の一手だけ / Claude が再加工する用の生JSON
python3 tools/notion_active_todos.py --task-type "NextAction 🚀"
python3 tools/notion_active_todos.py --category "Pole&Line" --json
```

- 出力は TaskType ごとにグルーピングし `[優先度][ステータス] タイトル / 開始日 / URL`。本文ブロックは取らない（軽量）。
- ToDo DB の `database_id` と `data source(collection) ID` は別物。本スクリプトは REST query 用に `database_id` を内蔵済み。
- Waiting（相手ボール）も「未完」なので既定で含む。NextAction と区別したいときは TaskType で見る。

## ToDo の起票（行作成・MCP 非依存）｜`notion_create_todo.py`

ToDo DB に1行を **NOTION_TOKEN 直叩き**で作る。MCP（`notion-create-pages`）は無人実行中に
`requires approval` で wedge する（CLAUDE.md §14）ため、**夜間ルーティンの確認タスク起票はこれを使う**
（agents/routines.md ①②③）。承認ゲートに当たらないのでヘッドレスでも確実。

```bash
# 既定: TaskType=Inbox 📨 / Category=Pole&Line / ステータス=未着手
python3 tools/notion_create_todo.py "[佐藤] 〇〇 プロファイル作成に確認要" --candidate <Notionページ ID>
# 企業案件・ポジション・スカウト等の relation も指定可（複数回指定で複数 relation）
python3 tools/notion_create_todo.py "[Claude] △△ の要確認" --client <pageid> --scout-todo
```

## 案件サーチの構造化取込（recall 担保）｜`sf_jobs_ingest.py`

SF の **open 案件**を取り込み、各 JD（`information__c`）を **Gemini で構造化**して
Notion DB『案件サーチ｜SFミラー』へ **SF案件ID で upsert** する（重複作成しない）。
候補者マッチの**網羅性（recall）**を担保するための母集団づくり。設計の背景と使い方は
`agents/client-profile.md` §7.5 と `agents/routines.md` ⑤ を参照。

- **なぜ要るか：** open 案件は約5,000件・職種 null が約2割・誤タグ／重複／イベント告知が
  混在し、クエリ時のキーワード絞り込みでは取りこぼす。取り込み時に一度だけ構造化すれば、
  マッチ時は構造化済みDBを読むだけで recall を担保できる。
- **コンテキスト非通過：** SF SOAP ログイン → REST 取得 → HTML 除去 → Gemini（JSON 強制）→
  Notion upsert まで**すべてスクリプト内で完結**。JD 全文を Claude のコンテキストに通さない。
- タグ体系は Craft『完成版｜求人構造化評価プロンプト（候補者DB互換）』が正本（候補者DBと同一軸）。

```bash
# 日次の増分（夜間ルーティン）。直近1日 ×openを upsert
python3 tools/sf_jobs_ingest.py

# 初回バックフィル（直近30日・約670件）
python3 tools/sf_jobs_ingest.py --days 30

# 小さく試す / 書かずに分類だけ見る
python3 tools/sf_jobs_ingest.py --days 1 --limit 5 --verbose
python3 tools/sf_jobs_ingest.py --days 1 --limit 3 --dry-run
```

- 必要な環境変数：`SALESFORCE_USERNAME/PASSWORD/TOKEN/INSTANCE_URL`・`GEMINI_API_KEY`・`NOTION_TOKEN`
  （DB『案件サーチ｜SFミラー』にインテグレーションを共有しておくこと）。MCP コネクタには依存しない。
- 鮮度は `CreatedDate`（新規起票日）で絞る。SF の `LastModifiedDate` は Bot が全件触り続けて死んでいる。
- `gemini-2.5-*` は thinking が既定 ON で出力が途中で切れるため、スクリプトは `thinkingBudget: 0` で無効化済み。

---

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

> 注: ③ の「テキストを入力に議事録生成」は現状 `gemini_transcribe.py` が音声入力前提
> のため、テキスト入力対応の薄いラッパ追加が必要（次段で実装予定）。

---

# Mac ローカル・ツール群｜`tools/mac/`

佐藤の **Mac（mini／MacBook）でローカル実行する**変換・録画処理スクリプト一式（OGG→MP3 自動変換、BlackHole 録画の動画処理＋文字起こし常駐、mono 音声補正、面談種別のプロンプト雛形）。**詳細・セットアップ・マシン固有の調整は `tools/mac/README.md` を参照。**

- 上の文字起こしパイプライン（`tools/` 直下）は**サーバ側**で Web/ローカル問わず動くのに対し、`tools/mac/` は **macOS 依存（ffmpeg・AppleScript・launchd・ローカルフォルダ監視）でそのMac上でしか動かない**。役割が別。
- そのため**実行できるのは各 Mac 上の VSCode/CLI 版 Claude Code のみ**。Web 版（クラウド）は読取・編集はできても実行不可。2台の Mac は `main` を pull すれば同じ最新版を共有できる。
