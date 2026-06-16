# 夜間 Routine 運用（使用量枠の最適活用）

就寝中のアイドル時間に、クラウド側の **Routine**（`claude.ai/code/routines`、CLI では `/schedule`）で
プロファイル backfill を自走させ、使用量枠を有効活用するための運用ルールと、登録済み Routine の正本。

> このファイルは「夜間に何が走っているか」を後続セッションが把握するための申し送り。
> プロファイル本体は **Craft が唯一の正本**（git には書かない）。ここに置くのは Routine の**文面と運用方針だけ**。

---

## 0. 何のための仕組みか

- Claude の使用量制限は **2層**：①5時間ローリング枠（その場で絞られる層）／②週次枠（総量の天井。全体枠＋**Opus 専用枠**の2本立て）。
- 夜にずらしても**週次枠は増えない**（同じプールを共有）。効くのは「日中の対話レスポンスを枠の奪い合いから守る」＝**時間配置の最適化**。
- したがって夜間 Routine は **Sonnet 主体**で回し、希少な **Opus 週次枠は日中の高難度タスク**（推薦文・マッチング評価・面談設計）に温存する。

---

## 1. 枠の仕組みと「朝をブロックしない」設計原則

5時間枠は **最初のメッセージで開き、5時間後に閉じる固定窓**。窓が開いている間に走る処理は新窓を開けず**相乗り**する。
窓の位相（リズム）は **就寝前のユーザー使用**で決まり、Routine 側からは制御できない。前夜の使い方次第で
**朝をまたぐ窓**（例：22:00 開始 → 3:00 → 8:00。6:30〜8:00 が同じ窓）が生じ得る。

→ **スケジュール調整だけでは「夜にフル消費 ＋ 朝は必ず新品」を完全には両立できない。**
　 そこで安全網の主従を入れ替える：

| 狙い | 手段 | 確実性 |
|---|---|---|
| 朝に絶対ブロックされない | **1晩の消費を1窓の6割程度に抑え、余力を残す（Sonnet）** | ◎ スケジュール非依存で確実 |
| 多くの夜で朝を新品にする | 1:00 台に1本だけ撃ち、後半（〜起床）は静寂 | ○ 当てにはできないが得 |
| 総量をフル活用 | **複数の夜で積み上げて週次枠を使い切る**（1晩で搾り取らない） | ◎ |

**原則：1晩は「早撃ち1本・余力残し・Sonnet」。総量は夜の本数（日数）で稼ぐ。**

---

## 2. 曜日で2階建て（平日朝だけ守る）

深夜 1:00 の起動は **同じ曜日の朝**（6:30）に影響する。ユーザーの要望は「月〜金の朝だけ守れればよい／土日朝は気にしない」。

| 起動（深夜 1:00 頃） | 影響する朝 | 扱い |
|---|---|---|
| 月・火・水・木・金 1:00 | 各曜日の朝 | 🛡 保護（控えめ・余力残す） |
| 土 1:00 | 土の朝 | 🔥 フル活用OK |
| 日 1:00 | 日の朝 | 🔥 フル活用OK |

→ 「金曜の夜（＝土 1:00）」「土曜の夜（＝日 1:00）」が解放枠。平日 5 回の朝は構造的に守り、
　 **土日 2 晩でバックログを一気に消化**する（総量は週末に front-load）。

**前提リズム（ユーザー）：** 平日就寝 24:30〜起床 6:00。土日は起床が 1〜2 時間遅い。

---

## 3. Routine 一覧（正本）

クライアントと候補者は **1 セッションで順番に処理**（窓を 1 本に束ねるため、時刻分割しない）。計 2 本。

### ① 平日夜間 backfill（🛡 控えめ）

- **スケジュール：** weekdays プリセット（月〜金）@ **1:00 JST**
- **モデル：** Sonnet 4.6
- **リポジトリ：** `pnl-sato/pnl`
- **コネクタ（claude.ai 連携）：** Craft / Notion / Gmail / Google Drive / Slack を含める（GitHub・Calendar は不要）。
- **Salesforce：** コネクタ一覧には**出ない／追加不要**。リポジトリの `.mcp.json` 経由で繋がる project スコープ MCP。
  動かすには **`SALESFORCE_USERNAME` / `SALESFORCE_PASSWORD` / `SALESFORCE_TOKEN` / `SALESFORCE_INSTANCE_URL` の4つの環境変数**が必要。
- **`NOTION_TOKEN`（重要）：** スキップ時の ToDo 起票は **MCP（`notion-create-pages`）ではなく `tools/notion_create_todo.py`（NOTION_TOKEN 直叩き）で行う**。
  無人実行中は MCP 書き込みが `requires approval` で wedge し、承認する人がいないため詰まる（CLAUDE.md §14）。直叩き REST は承認ゲートに当たらないので確実に起票できる。
  そのため**環境のシークレットに `NOTION_TOKEN` も入れておく**（`.env.example` 参照。通常このリポジトリの環境には登録済み）。
- **環境：** **これら SF 環境変数が設定済みの環境を選ぶ**（このリポジトリが通常使っている環境）。まっさらな Default を選ぶと SF だけ認証失敗するので注意。
  ネットワークは Trusted で可（npm レジストリ＋SF）。企業 Web への WebFetch が 403 になり得るが内部ソース中心の土台作成には支障なし。

**プロンプト（そのまま貼り付け）:**

```text
あなたは Pole&Line の業務エージェントです。リポジトリ pnl-sato/pnl の CLAUDE.md、
agents/client-profile.md、agents/candidate-profile.md を最初に読み、その手順に厳密に従ってください。

## 性質：これは「平日」夜間ランです（朝の枠を必ず守る）
- 朝6:30以降にユーザーが使うとき、枠切れにしないことが最優先。
- 5時間枠を使い切らないこと。1窓の6割程度に抑え、余力を残す。
- 遅くとも JST 3:30 までに作業を終える。重い処理を後半に引きずらない。
- 下記の上限（クライアント最大3社／候補者最大5名）を超えて作らない。達したら即終了。

## Part 1：クライアントプロファイル backfill（最大3社）
1. Salesforce でクライアント企業を一覧化する。**クライアント判定は agents/client-profile.md セクション 2.4 に従い `Contract_Status__c = '締結済み'` のみ**。SF の Account には候補者の在籍企業（職歴の会社）が大量に混在しており（約14,000件中クライアントは約170件）、null・未締結・締結対応中・終了の Account はクライアントではないので対象にしない。SOQL 例：`SELECT Id, Name, Contract_Status__c, Notion_Page_ID__c, ATS_URL__c, Craft_Profile_URL_SY__c FROM Account WHERE Contract_Status__c = '締結済み' AND Craft_Profile_URL_SY__c = null`（`Craft_Profile_URL_SY__c = null` ＝佐藤の Craft プロファイル未作成のものだけに決定論的に絞る。client-profile.md §2.5 判定0）。
2. 各社について **agents/client-profile.md セクション 2.5「既存判定の多重照合」を必ず実施**：
   SF `Account.Craft_Profile_URL_SY__c` 照合（判定0・決定論的・主キー。非空なら作成済み）→ SF Account ID 照合 → Notion Page ID 照合 → フォルダ名＋配下文書チェック。**作成したら／移行期に既存 doc を当てたら、必ず `Account.Craft_Profile_URL_SY__c` に rootBlockId を書き戻す（公開共有リンクは保存しない）。**
   いずれか1つでもヒットすれば「作成済み」とみなしスキップ（フォルダ名の表記揺れだけで「未作成」と判断しない）。
3. 「SF登録あり かつ 2.5 の多重照合すべて不一致」のみを抽出し、最大3社を選ぶ
   （優先順：注力フラグ > 関わり度合い高 > 直近動きのある順。材料が乏しければ社名昇順）。
4. 各社について agents/client-profile.md「初回生成モード」(セクション2・4)に従い、Notion 企業/ポジション/
   パイプライン/面談メモ DB、SF Account/matching__c、Gmail(個人＋SY/共有由来)、
   Google Drive「01_企業情報/{社名} #{社コード}/」、Slack を横断収集し、不足は WebSearch/WebFetch で補完して
   Craft に {社名}/{社名}.md を新規作成。**`{社名}` の命名は client-profile.md §2.5「命名ルール（SF Account.Name 基準）」に従う**
   （SF Account.Name を最初の `/`・全角スペースでトリムした文字列。既存の短縮名フォルダは遡及対象外＝新規からのみ）。
   注力 YES のポジションは positions/{ポジション名}.md も作成（注力 NO は md 化しない）。

## Part 2：候補者プロファイル backfill（佐藤所有・選考中アクティブ・最大5名）
0. 【対象制限・重要】候補者は必ず「佐藤 雄太が所有する SF パイプライン」に限定する。他コンサルタント所有は除外。
   （企業＝Part 1 は所有者を問わず従来通り全クライアント対象。この制限は候補者のみ）
1. SF matching__c を OwnerId = '0055h000004V2dtAAC'（佐藤 雄太 / sato-y@pnl.co.jp）
   AND phase__c NOT IN ('脱落','入社済み') で取得し、その Contact__c（候補者）を「アクティブ候補者」の正本とする。
   **取得項目に `Contact__r.Name`／`Contact__r.furigana_lastname__c`／`Contact__r.furigana_firstname__c`／`Contact__r.Craft_Profile_URL_SY__c` を含め、`Contact__r.Craft_Profile_URL_SY__c = null`（佐藤の Craft 未作成）に決定論的に絞る**（candidate-profile.md §2.5 判定0）。
   Notion パイプライン DB(collection://20f7d017-b6a0-807c-a60f-000b827c6841)は enrichment に併用してよいが、
   対象集合は必ず上記 SF（佐藤所有・アクティブ・Craft未作成）に交差させる。
2. Craft フォルダ「13_Candidate｜候補者」(folder ID: 05BC363C-0FC2-4B15-AB3D-7C335AA5AB4E)を search し、
   既にプロファイル md がある候補者を把握する（判定0が null でも、移行期は既存 doc がヒットしうる。その場合は作成せず `Contact.Craft_Profile_URL_SY__c` に rootBlockId を書き戻して次へ）。
2.5. 【氏名ガード・必須／candidate-profile.md §2.5】`Contact.Name` が**媒体会員ID・番号だけ**（全数字、または `A`＋数字で `furigana_lastname__c` と同値、実名なし）の候補者は**作らずスキップ**する（返信前スカウトで実体が無い）。実名はあるが漢字が無い（ローマ字氏名）場合は**ローマ字のまま**作成し、**ローマ字→漢字の当て字は絶対に作らない**。氏名は正本（レジュメ＞佐藤の Notion 候補者DB＞SF Name＋ふりがな）の表記をそのまま使う。
3. 「佐藤所有・アクティブ・Craft未作成 かつ 氏名ガード通過（実名あり）」を抽出し、最大5名を選ぶ（優先順：選考フェーズが進んでいる順 > 直近更新順）。
4. 各候補者について agents/candidate-profile.md「初回生成」(セクション5.2)に従い、Notion 候補者/パイプライン/
   面談メモ/選考評価/スカウト DB、SF Contact/matching__c、Gmail(個人＋SY/共有由来)、Slack を横断収集し、
   Craft に「{漢字氏名}（{ふりがな}）」で新規作成（**当て字禁止**）。9セクション構成・Craft投入の構文ルール(5.2)を厳守。
   情報がない項目は（未取得）と明記。LinkedIn DM はユーザー提供素材のみのため無ければ（未取得）。
   **作成したら `salesforce_dml_records` で `Contact.Craft_Profile_URL_SY__c` に rootBlockId を書き戻す（公開共有リンクは保存しない）。**

## 共通ルール
- 成果物は Craft のみ。リポジトリ(git)には一切書き込まない・コミットしない。
- Craft投入の構文：ブロック区切りは \n\n、markdown 先頭に --- を置かない、セクション単位で分割投入（candidate-profile.md 5.2）。
- 同名で本人/社が特定不能、必須情報欠落で安全に作れない対象はスキップし、ToDo を起票する。
  **起票は無人実行で MCP 書き込みが wedge する（CLAUDE.md §14）のを避けるため `tools/notion_create_todo.py`（NOTION_TOKEN 直叩き）を使う：**
  `python3 tools/notion_create_todo.py "[佐藤] {名称} プロファイル作成に確認要" --candidate <Notionページ ID>`（企業案件なら `--client`／ポジションなら `--position`）。
  TaskType=Inbox 📨・Category=Pole&Line は既定。NOTION_TOKEN が環境に無いときだけ MCP `notion-create-pages`（ToDo DB collection://2257d017-b6a0-8026-867c-000bb0969507）にフォールバック。
- 個人情報・クライアント内部評価を外部に出さない。「他社展開不可」等の指示は留意事項に明記。推測で年齢・年収を埋めない。

## 完了時の報告（セッション末尾に出力）
- 作成したクライアント／候補者と Craft URL の一覧
- スキップした対象と理由
- 対象が無ければその旨
```

### ② 週末夜間 backfill（🔥 フル）

- **スケジュール：** 土・日 @ **1:00 JST**（プリセットに weekends が無いので、weekly か daily で作成 → `/schedule update` で cron `0 1 * * 6,0` を設定。または `/schedule` に「土日の深夜1時」と自然言語で指定）。
  - **次回実行時刻が JST 土・日の 1:00 台になっているか UI で必ず確認**（cron が UTC 解釈される場合は時刻を調整）。
  - 任意：5:00 にもう 1 本撃って 2 窓目まで使い切ってもよい（週末朝は気にしないため）。
- **モデル：** Sonnet 4.6（品質最優先で Opus も可だが、**Opus 週次枠を削る**ため原則 Sonnet）
- リポジトリ／コネクタ／環境は ① と同じ。

**プロンプト（そのまま貼り付け）:**

```text
あなたは Pole&Line の業務エージェントです。リポジトリ pnl-sato/pnl の CLAUDE.md、
agents/client-profile.md、agents/candidate-profile.md を最初に読み、その手順に厳密に従ってください。

## 性質：これは「週末」夜間ランです（朝の枠は気にしない＝フル活用）
- 朝のブロックを気にせず、対象が尽きるか使用量枠が尽きるまで積極的に処理してよい。
- ただしモデルは Sonnet を維持し、Opus 週次枠は消費しないこと。

## Part 1：クライアントプロファイル backfill（最大10社、尽きたら終了）
1. Salesforce でクライアント企業を一覧化する。**クライアント判定は agents/client-profile.md セクション 2.4 に従い `Contract_Status__c = '締結済み'` のみ**。SF の Account には候補者の在籍企業（職歴の会社）が大量に混在しており（約14,000件中クライアントは約170件）、null・未締結・締結対応中・終了の Account はクライアントではないので対象にしない。SOQL 例：`SELECT Id, Name, Contract_Status__c, Notion_Page_ID__c, ATS_URL__c, Craft_Profile_URL_SY__c FROM Account WHERE Contract_Status__c = '締結済み' AND Craft_Profile_URL_SY__c = null`（`Craft_Profile_URL_SY__c = null` ＝佐藤の Craft プロファイル未作成のものだけに決定論的に絞る。client-profile.md §2.5 判定0）。
2. 各社について **agents/client-profile.md セクション 2.5「既存判定の多重照合」を必ず実施**：
   SF `Account.Craft_Profile_URL_SY__c` 照合（判定0・決定論的・主キー。非空なら作成済み）→ SF Account ID 照合 → Notion Page ID 照合 → フォルダ名＋配下文書チェック。**作成したら／移行期に既存 doc を当てたら、必ず `Account.Craft_Profile_URL_SY__c` に rootBlockId を書き戻す（公開共有リンクは保存しない）。**
   いずれか1つでもヒットすれば「作成済み」とみなしスキップ。
3. 「SF登録あり かつ 2.5 すべて不一致」のみを抽出し、最大10社（優先順：注力 > 関わり度合い高 > 直近動き > 社名昇順）。
4. 各社について agents/client-profile.md「初回生成モード」(2・4)に従い、Notion 企業/ポジション/パイプライン/面談メモ、
   SF Account/matching__c、Gmail(個人＋SY/)、Google Drive「01_企業情報/{社名} #{社コード}/」、Slack を横断収集し、
   不足は WebSearch/WebFetch で補完して Craft に {社名}/{社名}.md を作成（**`{社名}` は §2.5 の SF Account.Name 基準ルール**＝
   最初の `/`・全角スペースでトリム、既存短縮名は遡及せず新規から）。注力 YES は positions/{ポジション名}.md も作成。

## Part 2：候補者プロファイル backfill（佐藤所有のみ・最大20名）
0. 【対象制限・重要】候補者は必ず「佐藤 雄太が所有する SF パイプライン」に限定する。他コンサルタント所有は除外。
   （企業＝Part 1 は所有者を問わず従来通り。この制限は候補者のみ）
1. SF matching__c を OwnerId = '0055h000004V2dtAAC'（佐藤 雄太 / sato-y@pnl.co.jp）で取得。
   phase__c NOT IN ('脱落','入社済み') のアクティブを優先一覧化。**取得項目に `Contact__r.Name`／`Contact__r.furigana_lastname__c`／`Contact__r.furigana_firstname__c`／`Contact__r.Craft_Profile_URL_SY__c` を含め、`Contact__r.Craft_Profile_URL_SY__c = null`（佐藤の Craft 未作成）に決定論的に絞る**（candidate-profile.md §2.5 判定0）。Notion パイプライン DB
   (collection://20f7d017-b6a0-807c-a60f-000b827c6841)は enrichment に併用可。
2. Craft「13_Candidate｜候補者」(folder ID: 05BC363C-0FC2-4B15-AB3D-7C335AA5AB4E)を search し、既存 md を把握（移行期に既存 doc がヒットしたら作成せず `Contact.Craft_Profile_URL_SY__c` へ rootBlockId を書き戻す）。
2.5. 【氏名ガード・必須／candidate-profile.md §2.5】`Contact.Name` が媒体会員ID・番号だけ（全数字、または `A`＋数字で `furigana_lastname__c` と同値、実名なし）の候補者はスキップ。実名はあるが漢字が無い場合はローマ字のまま作成し、**ローマ字→漢字の当て字は絶対に作らない**。氏名は正本（レジュメ＞佐藤の Notion 候補者DB＞SF Name＋ふりがな）の表記そのまま。
3. 「佐藤所有・アクティブ・Craft未作成 かつ 氏名ガード通過」を優先抽出。アクティブが尽きたら、佐藤所有 matching__c に紐づく
   全候補者（フェーズ問わず／Contact__c）の Craft未作成へ広げてよい。佐藤所有外の候補者は作らない。合計最大20名。
4. 各候補者について agents/candidate-profile.md「初回生成」(5.2)に従い Craft に「{漢字氏名}（{ふりがな}）」で作成（**当て字禁止**）。
   9セクション構成・構文ルール(5.2)厳守。情報がない項目は（未取得）。LinkedIn DM は無ければ（未取得）。
   **作成したら `salesforce_dml_records` で `Contact.Craft_Profile_URL_SY__c` に rootBlockId を書き戻す（公開共有リンクは保存しない）。**

## 共通ルール
- 成果物は Craft のみ。git には書かない。Craft投入の構文ルール(candidate-profile.md 5.2)を厳守。
- 特定不能/必須情報欠落で安全に作れない対象はスキップし、ToDo を起票する。
  **起票は無人実行で MCP 書き込みが wedge する（CLAUDE.md §14）のを避けるため `tools/notion_create_todo.py`（NOTION_TOKEN 直叩き）を使う：**
  `python3 tools/notion_create_todo.py "[佐藤] {名称} プロファイル作成に確認要" --candidate <Notionページ ID>`（企業案件なら `--client`／ポジションなら `--position`）。
  TaskType=Inbox 📨・Category=Pole&Line は既定。NOTION_TOKEN が環境に無いときだけ MCP `notion-create-pages`（ToDo DB collection://2257d017-b6a0-8026-867c-000bb0969507）にフォールバック。
- 個人情報・クライアント内部評価を外部に出さない。「他社展開不可」等は留意事項に明記。推測で埋めない。

## 完了時の報告（セッション末尾）
- 作成したクライアント／候補者と Craft URL / スキップと理由 / 対象が無ければその旨
```

### ③ 隔週メタ点検（🩺 システム健全性・backfill とは別目的）

backfill（①②）がデータを作る Routine なのに対し、これは**設定とデータの健全性を点検して
Slack に短く報告する**Routine。成果物は作らない。山田（別エージェント）の `claude-usage-review`
相当を P&L 用に最小構成で持つもの。

- **スケジュール：** 隔週・平日朝（例 月曜 8:00 JST）。`/schedule` で「隔週月曜の朝8時」を指定するか、
  weekly で作成して隔週運用（次回実行が JST 月曜 8:00 台か UI で確認）。
- **モデル：** Sonnet 4.6（点検は軽い。Opus 週次枠は使わない）
- **リポジトリ：** `pnl-sato/pnl`
- **コネクタ：** Craft / Notion / Slack（Gmail・Drive は不要。GitHub 接続があれば main 未反映チェックが楽）。
  Salesforce は `.mcp.json` 経由（任意・候補者突合に使うなら SF 環境変数入り環境を選ぶ）。
- **出力先：** Slack の**指定チャンネル**（登録時に決める。運用/自分宛 DM 推奨）。

**プロンプト（そのまま貼り付け。{SLACK_CHANNEL} は登録時に置換）:**

```text
あなたは Pole&Line の業務エージェントの「運用点検役」です。リポジトリ pnl-sato/pnl の
CLAUDE.md と agents/ を最初に読み、この設定がどう使われる想定かを把握してください。
これは成果物を作るランではなく、設定とデータの健全性を点検して Slack に短く報告するランです。
トークンは節約し、全文展開はしない（CLAUDE.md §13。プロファイルは目次・冒頭のみ確認）。

## 点検する5項目（各 🟢/🟡/🔴 で採点し、根拠を1行ずつ）
1. スキル/エージェント稼働：agents/*.md と CLAUDE.md セクション5の導線のうち、参照切れ・
   実体なし・最近使われていないものを洗い出す（導線と実体の齟齬）。
2. プロファイル鮮度：Craft 候補者(13_)・クライアント(12_)の md で Last synced が古いもの
   （目安30日以上）や「（未取得）」が多い薄いものを件数で。全文は読まない。
3. Routine 稼働：夜間 backfill（①②）が直近で走り、重複作成や薄いコピーを生んでいないか
   （12_Client 直下の同名フォルダ重複・「特記事項なし」多発の兆候を軽くチェック）。
   あわせて**在籍企業の誤登録**（client-profile.md 2.4）の兆候も軽く確認：12_Client 配下の md に
   `Contract_Status__c = '締結済み'` でない Account（候補者の在籍企業）が混ざっていないか、
   サンプル数件の SF Account を契約状況で突合する。混在を検知したら件数を報告し ToDo 起票。
4. main 未反映：作業ブランチに main へ未マージの CLAUDE.md / agents 改訂が残っていないか
   （git で origin/main と claude/ ブランチの差分を確認。§12 の集約漏れ検出）。
5. ToDo 衛生：Notion ToDo DB の Inbox 未 triage 件数・長期 未着手・relation 欠落を集計。

## 出力（{SLACK_CHANNEL} へ1通、簡潔に）
- 日付＋5項目のスコア表（🟢/🟡/🔴）
- 🔴/🟡 は「何を・どうすると直るか」を1行ずつ
- 重大なものだけ ToDo を起票する（[佐藤] or [Claude]・Inbox 📨・該当 relation 付き）。
  **無人実行のため MCP ではなく `tools/notion_create_todo.py`（NOTION_TOKEN 直叩き・§14 の wedge 回避）で起票する：**
  `python3 tools/notion_create_todo.py "[Claude] {要点}" --client <Notionページ ID>`。NOTION_TOKEN が無いときだけ MCP `notion-create-pages`（ToDo DB collection://2257d017-b6a0-8026-867c-000bb0969507）にフォールバック。
- 問題が無い項目は「問題なし」と明記。憶測でスコアを下げない。
```

### ④ 週末 SF構造リフレッシュ（🗂 構造正本の更新・①②③とは別目的）

①②が Craft にデータを作り、③が点検して Slack に報告するのに対し、これは **`sf_structure.md`（SF スキーマの正本）を最新化する**Routine。成果物はこの **git 1ファイルのみ**（①②③は git に書かないが、④だけは git に書く点が決定的に違う）。狙いは、重い `salesforce_describe_object` を毎タスクに撒かず**週1回に閉じ込め**、平日タスクは蒸留済み md を読むだけにすること（CLAUDE.md §13）。

> **GitHub への上げ方：** Claude Code on the web の GitHub プロキシは **push を「いまチェックアウト中のブランチ（HEAD）」に限定**する（HEAD と違うブランチへの push は不可。main 自体は checkout すれば push できる）。④は**スキーマ変更を diff でレビューしてから main に入れたい**ので、**固定ブランチ `automation/sf-structure-refresh` に push → main への PR を開く/更新**し、**佐藤が手動マージ**する。差分が無い週は PR を作らない。

- **スケジュール：** 週1回・週末早朝（例 日 @ **2:00 JST**）。`/schedule` で「日曜の深夜2時」を指定、または weekly 作成 → cron `0 2 * * 0`（次回実行が JST 日 2:00 台か UI で確認）。②と同夜でも別 routine にする。
- **モデル：** Sonnet 4.6（describe の蒸留は軽い。Opus 週次枠は使わない）。
- **リポジトリ：** `pnl-sato/pnl`。
- **環境：** **SF 環境変数（USERNAME/PASSWORD/TOKEN/INSTANCE_URL）入りの環境必須**（①②と同じ）。まっさら Default は SF 認証失敗。
- **コネクタ：** Craft/Notion/Gmail 等は**不要**（SF は `.mcp.json` 経由）。
- **GitHub 接続：** ルーティン個別の設定は無い。**アカウント単位で一度繋げば足りる**（GitHub App 認可 or ターミナルの `/web-setup`）。`/schedule` は接続を自動利用し、未接続なら `/web-setup` を促す。作業ブランチへの push と PR 操作はこの接続で通る（**「git push 許可」という別トグルは存在しない**）。

**プロンプト（そのまま貼り付け）:**

```text
あなたは Pole&Line の業務エージェントの「SF構造メンテ役」です。リポジトリ pnl-sato/pnl の
CLAUDE.md と sf_structure.md を最初に読み、sf_structure.md を最新の SF スキーマで更新するランです。
成果物はこの1ファイルのみ。トークン節約：describe の生JSONは蒸留して保存し、全文は文脈に残さない。

## 手順
1. 追跡対象オブジェクトを列挙する（sf_structure.md「追跡対象オブジェクト」のスコープに従う）。
   - カスタム：QualifiedApiName LIKE '%__c' のオブジェクトを名前だけ一覧化（この段階で describe しない）。
   - 標準：allowlist（既定 Account / Contact / User）。
2. 各対象を salesforce_describe_object で取得し、次だけ蒸留する（監査/システム項目
   CreatedBy* / LastModified* / SystemModstamp 等は除外。**フィールドの全表は作らない**＝
   それは agents/salesforce.md の既定項目セットが持つ。本ファイルは選択肢と増減に集中する）：
   - 各 picklist フィールドの選択肢値（API値）→「選択肢値カタログ」を更新。とくに
     Account.Contract_Status__c, matching__c.phase__c / DropReason__c,
     Opportunity.StageName / Jobtype__c / priority__c / typeofcontract__c は必ず列挙する。
   - フィールド・オブジェクトの増減（新規カスタムオブジェクト、追加/削除フィールド、型変更）。
   - 参照関係（reference の referenceTo）の変化。
3. 蒸留結果で sf_structure.md の「選択肢値カタログ」「オブジェクト一覧」「追跡対象オブジェクト」を更新する。
   固定セクション（全体概要・よく使うSOQL・設計上の特徴）は保持。describe で確認できた選択肢は
   「暫定」「要列挙」表記を外す。ヘッダの「最終更新」を Today（context の Today's date）に更新する。
   あわせて **agents/salesforce.md の既定項目セットのフィールドが describe に実在するか突合**し、
   消えた／改名されたフィールドがあれば完了報告に挙げる（salesforce.md 自体の手当ては別途）。
4. 既存 sf_structure.md と差分を取り、**実質的な変更があるときだけ**先へ進む
   （フィールドの増減・型変更・選択肢の追加削除・新規カスタムオブジェクト等）。差分が無ければ
   何もせず「変更なし」で終了する（ブランチも PR も作らない）。
5. 変更がある場合：固定ブランチ `automation/sf-structure-refresh` を main から作り直す（既存なら main に
   合わせてリセット）→ そのブランチを checkout → sf_structure.md を commit → push（push は checkout 中の
   ブランチにのみ通るので、必ずこのブランチに切り替えてから push）。コミットメッセージ例
   「chore: refresh sf_structure.md (YYYY-MM-DD)」。
6. そのブランチから **main への PR を開く（既に開いていれば push で更新）**。タイトル「chore: refresh
   sf_structure.md (YYYY-MM-DD)」、本文に「追加/削除/変更された選択肢・フィールド・オブジェクト」を箇条書き。
   **マージはしない**（佐藤が diff をレビューして手動マージする）。

## 完了時の報告（セッション末尾）
- 更新の有無。更新した場合は、追加/削除/変更されたオブジェクト・フィールド・選択肢を箇条書きで要約。
- 新規に見つかったカスタムオブジェクトがあれば名前を列挙。
- 変更が無ければ「変更なし（スキーマ安定）」と明記。
```

### ⑤ 夜間 案件サーチ取込（🔎 SF案件→Notion構造化ミラー・①〜④とは別目的）

①②が Craft にプロファイルを作り、③が点検、④が SF スキーマ正本を更新するのに対し、これは **SF の open 案件を Gemini で構造化して Notion『案件サーチ｜SFミラー』へ取り込む** Routine。狙いは候補者マッチの**網羅性（recall）担保**（設計の背景は `agents/client-profile.md` §7.5）。成果物は Notion（git には書かない）。

- **何をするか：** `tools/sf_jobs_ingest.py` が SF SOAP ログイン → `StageName='open' AND CreatedDate=LAST_N_DAYS:1` を全件取得 → 各 JD（`information__c`）を Gemini で Craft『完成版｜求人構造化評価プロンプト（候補者DB互換）』のタグ体系に構造化 → Notion DB へ **SF案件ID で upsert**（重複作成しない）。**JD 全文は Claude のコンテキストに通さない**（スクリプト内で完結）。
- **なぜ取り込み型か：** open 案件は約5,000件・職種 null が約2割・誤タグ／重複／イベント告知が混在し、クエリ時のキーワード絞り込みでは取りこぼす。取り込み時に一度だけ構造化すれば、マッチ時は構造化済みDBを読むだけで recall を担保できる（§7.5）。
- **スケジュール：** 毎日 @ **1:30 JST**（①②の backfill と同夜で可。別 routine にする）。`/schedule` で「毎日深夜1時半」、または daily 作成。
- **モデル：** スクリプトが Gemini を直接叩くので、起動役の Claude は **Sonnet 4.6**（軽い。Opus 週次枠は使わない）。
- **リポジトリ：** `pnl-sato/pnl`。
- **環境：** **SF 環境変数（USERNAME/PASSWORD/TOKEN/INSTANCE_URL）＋ `GEMINI_API_KEY` ＋ `NOTION_TOKEN` が入った環境必須**（①②と同じ環境でよい。まっさら Default は不可）。
- **コネクタ：** 不要（SF/Gemini/Notion すべてスクリプトが環境変数で直叩き。MCP コネクタには依存しない＝無人実行で wedge しない）。

**プロンプト（そのまま貼り付け）:**

```text
あなたは Pole&Line の業務エージェントです。リポジトリ pnl-sato/pnl の tools/sf_jobs_ingest.py を
使って、SF の open 案件を Notion『案件サーチ｜SFミラー』へ構造化取り込みするランです。
JD 全文はコンテキストに載せない（スクリプトが直接 API を叩く）。トークンは最小限。

## 手順
1. 直近分を取り込む：`python3 tools/sf_jobs_ingest.py --days 2`
   （前夜失敗時の取りこぼし防止に2日窓。SF案件IDで upsert するので重複しない）
2. スクリプトの完了行（created/updated/JD無し/error の件数）を確認する。
3. error が多い（>対象の2割）場合のみ、`tools/notion_create_todo.py` で
   「[Claude] 案件サーチ取込でエラー多発・要確認」を Inbox 📨 で起票する（NOTION_TOKEN 直叩き・§14）。

## 完了時の報告（セッション末尾）
- created / updated / JD無し / error の件数。error があればサンプル1〜2件の理由。
- 正常時は「取込OK（created N / updated M）」と1行で。
```

> **初回バックフィル：** 本運用前に一度だけ `python3 tools/sf_jobs_ingest.py --days 30` を回して直近30日（約670件）を入れておく（手動 or Run now）。以降は日次 `--days 2` で増分が乗る。

---

## 4. 登録手順（`claude.ai/code/routines` → New routine）

> このリモート Web セッション内からは `/schedule` を直接実行できない（routine 管理は Web UI 側）。
> ローカル CLI からなら `/schedule` で会話的に作成・更新も可能。

1. **New routine** → 名前（例「夜間backfill（平日・控えめ）」）を付ける。
2. 上記プロンプトを貼り、**Model = Sonnet 4.6**。
3. **Repository = `pnl-sato/pnl`**。
4. **Environment**：**Salesforce の環境変数（USERNAME/PASSWORD/TOKEN/INSTANCE_URL）が入った環境**を選ぶ（このリポジトリが通常使う環境）。まっさら Default だと SF だけ失敗。Web 補完を強化したい場合は Custom/Full に上げる。
5. **Connectors**：Craft / Notion / Gmail / Google Drive / Slack を含める（他は外す）。**Salesforce はここには出ない＝追加不要**（`.mcp.json` 経由で繋がる）。
6. **Trigger = Schedule**：① は weekdays @ 1:00 / ② は土日 @ 1:00（cron `0 1 * * 6,0`、次回実行が JST 土日 1:00 台か確認）。
7. **Permissions / GitHub**：①②③は git に書かないので既定のまま。**④（SF構造リフレッシュ）は git に書く**が、専用トグルは無い——**アカウント単位の GitHub 接続（GitHub App 認可 or `/web-setup`）が済んでいれば、ブランチへの push と PR 作成は通る**。プロキシは push を checkout 中のブランチに限定する（main も checkout すれば push 可能だが）。④はレビューしてから入れたいので `automation/sf-structure-refresh` ブランチ＋PR で上げ、**佐藤が手動マージ**する。
8. **Create**。**Run now** で 1 回試走し、セッション末尾の報告で品質・件数感を確認してから本運用へ。

**idempotency：** 毎回 Craft と照合して未作成だけ作るので、毎日回しても重複しない。一巡すれば「対象なし」で軽く終わる。

---

## 5. 運用時の注意

- 立ち上げ初期は ① を 1〜2 日試走し、朝の体感（6:30 以降に枠切れが起きないか）と出来を確認してから ② のフル運用へ。
- バックログが捌けたら、① は「アクティブ候補者の同期更新」中心に役割を移してよい（同期は client/candidate profile.md の「同期更新」モード）。
- 週次枠（特に Opus）が逼迫したら ② の件数を絞る。夜間は常に Sonnet。
- このファイルや運用方針を改訂したら、CLAUDE.md セクション 11 に従い **main へ反映**して後続セッションへ共有する。
