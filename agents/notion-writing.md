# Notion 書き込み（notion-create-pages / notion-update-page 等）の落とし穴と対処

Notion MCP への書き込みは、(a) コネクタ側で `requires approval` に wedge する事象と、(b) ページ本文の enhanced markdown 書式・parent 種別の取り違えで壊れる事象がある。
このファイルは、**実セッションで踏んだ Notion 書き込みの失敗→成功事例を集合知として蓄積する正本**。次回以降の書き込みで同じ罠にハマらないようにするための運用メモ。

- **Notion 書き込みの失敗はこのファイル**、**Craft 書き込みの失敗は `agents/craft-writing.md`** に、それぞれ独立したケース台帳として貯める（佐藤指示：書き込み先は Craft と Notion がほぼ全てなので、媒体ごとに集合知管理する）。
- 両者に共通する「host connector の書込が `requires approval` で wedge する事象」は **`CLAUDE.md` §14** が正本。ここからは相互参照にとどめ、対処の詳細は §14 を見る。

**台帳の境界線ルール（どこに何を書くか／いつ新ファイルを作るか）：**

- **媒体固有の format・behavior の失敗**（enhanced markdown のタブ字下げ・parent 種別の取り違え など Notion 特有のもの）→ **その媒体の台帳**（Notion はこのファイル）に書く。
- **媒体横断の connector wedge**（`requires approval`）→ **`CLAUDE.md` §14 のみ**に集約し、台帳からは相互参照（N1/N2 のように索引行＋要点だけ置く）。両台帳に実体を二重記載しない。
- **新しい書き込み先**（Gmail 下書き・Calendar・Slack・SF・Drive 等）→ **先回りで台帳を作らない**。実際に書き込み失敗が再発したら初めて専用台帳を起こす。それまで connector 系の失敗は §14 を catch-all にする。

---

## 0. セッション別ケース台帳（集合知）

新しい失敗を踏んで解決したら、まずこの表に**1行追記**してから（必要なら）下の詳細セクションを更新する。

| # | 日付・セッション | 失敗症状 | トリガー（やったこと） | 成功手順（直し方） | 恒久ルール参照 |
|---|---|---|---|---|---|
| N1 | 2026-06 monetization 引き継ぎ（`handoff-monetization-100man.md`） | `notion-create-view` が `Error: requires approval` で**5回連続失敗**、ビュー作成不可。読み取り・SF 読みは可能 | リモート Web セッションで Notion 書込系 MCP を叩いた（口頭許可はツール権限に反映されない） | リトライ連打しない／読取で生死を切り分け／成果物と保存仕様を Craft `_引き継ぎ｜未保存タスク` に退避／新セッションで再開 | §1, `CLAUDE.md` §14 |
| N2 | 2026-06 一般化（§14 正本） | host connector 書込が `requires approval`／`Streamable HTTP error … requires approval` で wedge。**読み取りは通るのに書き込みだけ落ちる**のが典型 | コネクタ側の書込同意／MCP セッションの失効。settings.json の allow に入っていても起きる＝settings では直らない | リトライ最大1回→`notion-fetch` 1本で読取の生死確認→ユーザー通知（チャットに承認ダイアログは出ない）→Craft へ退避→新セッション | §1, `CLAUDE.md` §14 |
| N3 | 成功パターン（恒久回避策） | MCP 書込が wedge でも**記録を落とさない** | — | **`NOTION_TOKEN` 直叩き REST（MCP 非依存）**の `tools/` スクリプトで後追い書き込みする。§14 のコネクタ同意問題を構造的に回避できる | §2 |
| N4 | enhanced markdown 書式 | トグル直下の子がネストされず、平のブロックになる | トグル子をタブ字下げせずに投入した | トグル直下は**タブ字下げ**で入れ子にする（Notion enhanced markdown。`notion-fetch` で round-trip 確認済みの書式） | §3 |
| N5 | parent 種別の取り違え | `notion-create-pages` が意図しない場所に作られる／エラー | DB に行を作るのに `page_id` を、ページの子ページを作るのに `data_source_id` を渡す等 | **DB に行を作るなら parent=`data_source_id`**、**ページの子ページなら parent=`page_id`**。例：送信ログ DB＝data source `437a2bad-94d6-4397-8dc4-1aa986a34c3d` | §3 |
| N6 | 2026-06-16 夜間ルーティンの ToDo 起票を直叩き化 | 無人ルーティンの ToDo 起票が MCP `notion-create-pages` だと `requires approval` で wedge し、承認者不在で詰まる（朝の wedge 持ち越しの一因にもなりうる） | ルーティン（routines.md ①②③）が MCP で Notion ToDo 起票していた | 起票を `tools/notion_create_todo.py`（NOTION_TOKEN 直叩き）へ移行。承認ゲートに当たらずヘッドレスでも確実 | §2, `CLAUDE.md` §14 |
| N7 | 2026-06-19 パイプライン title の命名規則違反 | Claude が作成したパイプライン行の title が `{候補者名} - {企業コード}` 規則を無視（社名フル・ポジション名・肩書きで命名）。254行中44行が `パイプライン-タイトル用` formula と不一致 | パイプライン作成時に title を手打ちし、`タイトル生成` ボタン／formula 出力に合わせていなかった | title は必ず `{候補者名} - {企業コード}`（例 `小島 洋平 - THR`）。作成時に `候補者`＋`ポジション` リレーションを張り、title を formula 出力に一致させる。一括是正は `NOTION_TOKEN` 直叩き REST で `名前` を formula 値に更新（29件）。コード空（企業DBコード未設定／ポジション未リンク）の行は上流を先に直す | `notion_structure.md` §3パイプラインDB「★パイプライン命名規則」 |

---

## 1. `requires approval` で書き込みが wedge する（最頻・最重要）

詳細な切り分けと退避・再開フローは **`CLAUDE.md` §14 が正本**。ここでは要点だけ：

- **読み取りは通るのに書き込みだけ落ちる**のが典型。原因はコネクタ側の書込同意／MCP セッション失効で、**settings.json では直らない**（権限ではなくコネクタ側の問題）。
- **リトライを連打しない**（一度 wedged になると同一セッションでは復帰しにくい。再試行は最大1回）。
- `notion-fetch` を1本だけ叩いて「読めて書けない」を確認＝書込同意の失効と切り分ける。
- すぐユーザーに通知する（**チャットに承認ダイアログは出ない**ので、放置すると詰まる）。
- 成果物と保存仕様（collection／page・各プロパティ）を Craft `work > 10_Recruitment > _引き継ぎ｜未保存タスク`（folder ID: `A50317A7-DDAA-42D2-A0DA-6093E8F60AEC`）に日時付きで退避し、新セッションで「未保存タスクの引き継ぎから再開」を案内する。

---

## 2. 成功パターン：`NOTION_TOKEN` 直叩き REST で wedge を構造的に回避する

§1 の wedge は MCP コネクタ経由でのみ起きる。**MCP を経由しない `NOTION_TOKEN` 直叩きの REST API なら承認ゲートに当たらない**。going-forward の記録系はこちらに寄せてあるので、MCP 書込が落ちても後追いできる。

| スクリプト | 用途 |
|---|---|
| `tools/scout_log.py` | スカウト送信ログ DB に「送信」を1行追加（`--template`／`--scout-id`／`--count`／`--media`） |
| `tools/scout_eval.py` | 送信ログ DB へ create／backfill（`create --from-block "…"` で「登録ブロック（パイプ1行）」をそのまま取り込み。`backfill --media-id … --reply-date … --result …` で結果書き戻し） |
| `tools/notion_active_todos.py` | ToDo DB の残タスク取得（読み取り。ステータス≠完了で正本化） |
| `tools/notion_create_todo.py` | ToDo DB に1行作成（`"[佐藤] …"` ＋ `--task-type`／`--category`／`--client`/`--candidate` 等の relation）。**無人実行のルーティン起票はこれ**（MCP の wedge 回避）。 |
| `tools/notion_append_text.py` | 指定ページ／ブロックへテキスト追記 |

**運用の型（scout-kit と対）：** Claude.ai／MCP 側で `notion-create-pages` が通らない時のフォールバックとして、評価出力の末尾に **§4.5 の「登録ブロック（パイプ1行）」をテキストで必ず残す**。後で佐藤が貼るか、Claude Code が `scout_eval.py create --from-block "…"` で取り込む。詳細は `agents/scout-kit.md` §4.5／§6.5。

---

## 3. ページ本文の書式・parent 種別の罠

### 3-1. トグル直下の子はタブ字下げで入れ子にする（enhanced markdown）

Notion の enhanced markdown では、トグル直下の子ブロックは**タブ字下げ**で表現する（`notion-fetch` で round-trip 確認済み）。字下げしないと子がネストされず平のブロックとして並ぶ。

```
+ トグルタイトル
	- 子 bullet 1
	- 子 bullet 2
```

> Craft のトグルは「枠を作ってから子を別コールで `blocks add --id <トグルID>`」の二段方式（`craft-writing.md` §3-5）で、**Notion とは作り方が違う**点に注意。媒体を取り違えて同じ書式を使い回さない。

### 3-2. `notion-create-pages` の parent は「行」か「子ページ」かで変える

- **DB に1行（レコード）を作る** → parent＝`data_source_id`（例：送信ログ DB＝`437a2bad-94d6-4397-8dc4-1aa986a34c3d`、スカウト DB＝`2597d017-b6a0-801b-8185-000ba4b9661e`）。プロパティを指定して作る。
- **既存ページの子ページを作る** → parent＝`page_id: {親ページID}`（例：スカウト文はポジションページの子ページ。プロパティは `title` のみ）。

主要 DB の data source ID と既定プロパティは `notion_structure.md`／`agents/scout.md`／`agents/scout-kit.md` が正本。実在しないプロパティ・選択肢を推測で作らない。

---

## 4. このファイルに更新があった時（集合知の貯め方）

Notion 書き込みで**新しい失敗を踏んで解決した**ら、ユーザーの指示がなくても：

1. **§0 のケース台帳に1行追記**する（日付・セッション／失敗症状／トリガー／成功手順／恒久ルール参照）。これが最優先・最小コストの蓄積口。
2. 再発防止に書式やコマンドの一般則が要るなら、§1〜§3 の該当箇所も更新する。
3. CLAUDE.md §12 の運用ルールに従い、**main にも fast-forward 反映**する（他セッションが参照できるよう）。

Craft 書き込みの失敗は本ファイルではなく **`agents/craft-writing.md`** の台帳に追記する。`requires approval` の wedge は両者共通なので **`CLAUDE.md` §14** に集約し、ここからは相互参照にとどめる。

**過去セッションの backfill：** 既に終わったセッションが踏んだ「静かな失敗」を後追いで台帳に貯めたいときは、`prompts/self-audit-write-failures.md` を**その過去セッションを resume して貼る**（そのセッション自身に自分の書き込みを自己点検させ、台帳行をテキスト出力させる）。出力された行をここ §0 に集約する。
