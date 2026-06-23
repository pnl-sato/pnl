# CLAUDE.md

このリポジトリは佐藤 雄太（Pole&Line合同会社）の業務エージェント用ベース設定です。
新しいセッションで Claude が自動で読む共通前提と、用途別ファイルへの導線をまとめています。

---

## 1. ユーザープロフィール

- 氏名：佐藤 雄太（SATO Yuta）
- 所属：Pole&Line合同会社（P&L）
- 役割：採用エージェント／ヘッドハンター
- 連絡先：sato-y@pnl.co.jp / Mobile 080-3930-1714
- 注力領域：ハイレイヤー人材（CxO・部長クラス・経営企画・人事・セキュリティ など）

## 2. 業務概要

クライアント企業から依頼されたポジションに対し、候補者をマッチングし選考プロセスを進めるヘッドハンティング業務。
主要な成果物：スカウト文、推薦文／推薦状、クライアントとのメール／打ち合わせ文章、面談メモ・打ち合わせメモ。

## 3. ツール構成と役割分担

| ツール | 役割 |
|---|---|
| **Notion** | 業務DBの正本（企業・ポジション・候補者・パイプライン・選考進捗・KPI） |
| **Craft** | 文章作成の作業場（推薦状・スカウト文の下書き、面談メモ、AIプロンプト管理） |
| **Gmail** | クライアント／候補者とのメール |
| **Slack** | 社内コミュニケーション |
| **Salesforce** | CRM（一部の管理）。読み込み時の既定項目セットは `agents/salesforce.md` を参照 |

Notion の DB 構造詳細は `notion_structure.md` を参照。

## 4. 共通の文体ルール

**コンパクション耐性のある核心ルールは `.claude/rules/writing-style.md` に定義済み（コンパクション後も再注入される）。**

補足：
- 出力は Craft のマークダウン記法に貼り付ける前提でフォーマットする
- 正式署名（参考。Claude は署名を付けない）：`Pole&Line合同会社 佐藤 雄太｜SATO Yuta Mobile：080-3930-1714`
- 詳細な文体ガイドは `agents/style.md` を参照

## 5. 用途別エージェント（必要に応じて読み込む）

タスクに応じて以下のファイルを読み込んで作業すること：

- **スカウト文を作成・修正する場合** → `agents/scout.md`（直近テンプレは Notion スカウト DB `collection://2597d017-b6a0-801b-8185-000ba4b9661e` が正）
- **スカウト評価キット生成** → `agents/scout-kit.md`
- **転職DB検索フィルタ作成** → 本書§11 と Craft `work > 10_Recruitment > スカウト媒体` 配下のDB別マスター
- **推薦文・推薦状** → `agents/client-writing.md`「推薦文」セクション
- **クライアントへのメール・打ち合わせ文章** → `agents/client-writing.md`「クライアント連絡」セクション
- **候補者宛メール** → `agents/candidate-mail.md`
- **候補者プロファイル作成・追記・同期** → `agents/candidate-profile.md`
- **クライアント／ポジションプロファイル・マッチング評価** → `agents/client-profile.md`
- **候補者面談のトークスクリプト・進行案・面談メモ** → `agents/interview-stance.md`
- **カレンダー登録**（選考面接）→ `agents/calendar.md`、（候補者インタビュー）→ `agents/candidate-mail.md`
- **営業戦略・上位方針** → `agents/work-approach.md`
- **1on1準備・報告** → `agents/one-on-one.md`
- **Weekly全体MTG準備** → `agents/weekly-meeting.md`
- **Craft 書き込み** → `agents/craft-writing.md`（改行・bullet の落とし穴と確実な構文）
- **Notion 書き込み** → `agents/notion-writing.md`（enhanced markdown・parent 種別の罠）
- **音声文字起こし／議事録** → `agents/transcription.md`
- **Mac ローカルツール** → `tools/mac/README.md`（VSCode/CLI 版限定）
- **朝の ToDo 生成** → `agents/daily-todo.md`
- **Notion 読み書き** → `notion_structure.md`
- **Salesforce 読み込み** → `agents/salesforce.md`（既定項目セット。全項目取得は禁止）
- **Salesforce スキーマ・SOQL** → `sf_structure.md`

## 6. 動作の原則

**核心の制約は `.claude/rules/safety-and-accuracy.md` に定義済み（コンパクション後も再注入される）。**

補足：
- 候補者の経歴・面談メモが添付されている場合、その内容を踏まえてから書く。情報不足時は質問する
- 出力は Craft のマークダウン記法に貼り付ける前提でフォーマットする

## 7. 候補者プロファイルの自動読み込み（重要）

**会話中で候補者の氏名（姓だけでも可）が言及された時点で、指示がなくても自動実行する。** 詳細手順は `agents/candidate-profile.md` 参照。

- Craft フォルダ `13_Candidate｜候補者`（folder ID: `05BC363C-0FC2-4B15-AB3D-7C335AA5AB4E`）を search → **depth-1 概観**を取得 → 必要なトグルだけ名指し展開。全文（`--depth 10`）一括展開は佐藤から明示要求があったときに限る
- 見つからない場合は即新規作成せず、`agents/candidate-profile.md` §2.5「既存判定の多重照合」を実行。未作成と確定したときだけ初回生成モードで横断調査して新規作成
- 候補者プロファイルは Craft が**唯一の正本**。git への複製は禁止
- 例外：単なる検索的質問（「小林さんって何人いたっけ」等）はスキップ可

## 8. クライアント／ポジションプロファイルの自動読み込み（重要）

**会話中でクライアント企業名またはポジション名が言及された時点で、指示がなくても自動実行する。** 詳細手順は `agents/client-profile.md` 参照。

- Craft フォルダ `12_Client｜企業`（folder ID: `F41A3C28-9B9B-47C7-BAC1-0D8431AF73A9`）配下を search → **depth-1 概観**を取得 → 必要なトグルだけ名指し展開。全文一括展開は明示要求時またはマッチング評価の横断比較時のみ
- 該当なしなら `agents/client-profile.md`「初回生成モード」に従い新規作成
- **マッチング評価**は `agents/client-profile.md` §7 の5軸（要件・カルチャー・年収・フェーズ・過去パターン）で構造化評価し、鮮度（🟢/🟡/🔴）をリスク注記として併記
- 例外：検索的質問はスキップ可

## 9. 1on1 議事録の自動読み込み（重要）

**トリガーキーワード「1on1」「上長」「代表」「議事録」「方針確認」等が言及されたら自動実行する。** 詳細手順は `agents/one-on-one.md` 参照。

- Notion 1on1 DB（`collection://22c7d017-b6a0-8033-837e-000bd6c2ae8b`）からまず最新1件の議事録を取得。過去経緯が必要なら直近3件まで
- 議事録が空欄なら Notion 文字起こし DB（`collection://3647d017-b6a0-80d7-b611-000ba74d7267`）から要約しながら読む
- 1on1準備タスクなら Craft `1on1 報告フィード｜斉藤さん`（rootBlockId `d0764d9e-2cdf-3222-d09d-d8475cd1984a`）も参照
- 例外：単なる日程確認はスキップ可

## 10. ToDo は Notion ToDo DB で一元管理（重要）

**核心の制約は `.claude/rules/todo-management.md` に定義済み。** 以下は補足運用ルール。

- Title プレフィックス：`[Claude]`（Claude完結）/ `[佐藤]`（本人実行）
- relation で文脈を紐付ける：`クライアント` / `ポジション` / `候補者` / `スカウト文`
- ステータス：`未着手` / `進行中` / `完了`。TaskType は原則 `Inbox 📨` で作成（GTD ワークフロー）
- `--category "Pole&Line"`（include 方式）は使わない。`--work`（exclude 方式）を使う
- 朝の業務一覧は `python3 tools/notion_active_todos.py --work`。詳細は `agents/daily-todo.md`
- セッションをまたぐアクションは必ず ToDo DB に起こす

## 11. 転職DB検索フィルタ作成時のマスター参照（重要）

検索フィルタ作成・調整の際は、Craft `work > 10_Recruitment > スカウト媒体`（folder ID: `E87A0808-42C9-4304-AB9E-32A39A50F2F7`）配下のDB別マスターを正本とし、実在しない選択肢を推測で作らない。

- **ビズリーチ**（folder ID: `819033D3-ADB1-493E-AD1D-6B2123A7025A`）：職種・業種・年収・学歴・英語力・マネジメント経験・登録社数の各マスターデータ
- 手順：`documents list --folder <サブフォルダID>` → 必要な条件のマスターを `blocks get --format markdown` で展開 → マスター内の選択肢のみでフィルタを組む

---

## 12. セッション横断の記録は main に集約する

`CLAUDE.md`・`agents/*.md`・`notion_structure.md` 等の変更は `main` に fast-forward マージしてプッシュする。作業ブランチに留める変更は一時的な実験・PoC のみ。

**肥大化を防ぐ「追記時統合」ルール（2026-06 佐藤指示）：** (1) 同種ルールが3つ以上溜まったら1つに統合 (2) 矛盾・陳腐化は最新で上書き (3) CLAUDE.md は「原則と導線」に保ち、詳細は `agents/*.md`（遅延ロード）や `.claude/rules/`（コンパクション耐性）へ逃がす。

## 13. セッション運用とトークン効率（重要）

Claude は**指示がなくても能動的にセッションの使い方を管理する**。目的はトークンの節約。

**判断は「差し引き」で決める：** 切る価値 ＝（もう参照しない重いコンテキスト＝死荷重）−（続行に要る再ロード＝買い直しコスト）。プラスのときだけ切る。会話先頭はプロンプトキャッシュが効く（入力の約1/10）ので蓄積コストは見た目より緩やか。

- **切る**：`/compact` 発火時／重い使い捨て読み込み完了時／次が別の重い文脈へ移るとき
- **切らない**：関連作業の連続（同じ md を読み直す＝買い直し）／重いコンテキストを直後に再利用するとき
- **毎セッション**：入口でプラン1行宣言、切りどき・続行を Claude から提案、読み込みは常に最小から

## 14. MCP 書き込み障害対処

`requires approval` で書き込みが止まったら、指示がなくても即対処する。原則：**リトライ連打しない→読み取り確認→ユーザー通知→成果物退避→引き継ぎプロンプト出力**。成果物の Craft 退避に加え、新セッションにそのまま貼れる自己完結プロンプト（成果物全文＋保存先＋プロパティ）をコードブロックで出力する。詳細手順・テンプレートは `agents/mcp-failure-handling.md` §1.1 参照。

## 15. 候補者面談のカレンダー予定フォーマット

候補者インタビューのカレンダー登録は統一フォーマットで作成する。詳細は `agents/candidate-mail.md`「カレンダー登録フォーマット」参照。

- タイトル：`［Online:{所要分}］インタビュー ({候補者ローマ字氏名})`
- 説明欄：固定の連絡先文のみ（内部情報は書かない）
- `addGoogleMeetUrl=true`、`notificationLevel=NONE`、候補者は既定でゲスト追加しない
