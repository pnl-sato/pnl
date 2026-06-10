# Salesforce 構造ドキュメント（sf_structure.md）

> **役割の分担（重要）：** SF の「**読み方＝オブジェクト別の既定項目セット**」は `agents/salesforce.md` が正本。本ファイルはそれを補完する**スキーマ参照**で、describe が一番効く部分＝**選択肢（picklist）値のカタログ**・**よく使う SOQL**・**オブジェクト/フィールドの増減**を週次で最新化して持つ。フィールド名の一覧表は salesforce.md と重複させない（あちらを参照）。SF を読むタスクは `agents/salesforce.md`、WHERE 句の選択肢値や SOQL の組み立て・スキーマ変更の確認はこのファイル、と使い分ける。
>
> **更新方式：** `agents/routines.md`「④ 週末SF構造リフレッシュ」が**週1回**、対象オブジェクトを `salesforce_describe_object` で取得→**選択肢値・フィールド増減だけ蒸留**→**差分があるときだけ main を更新**する。重い describe を週1セッションに閉じ込め、平日タスクはこの蒸留版を読むだけにする（CLAUDE.md §13）。
>
> **最終更新：** 2026-06-10（選択肢値カタログを**ライブ describe で確定**。Account / Contact / Opportunity / matching__c の picklist 実値を反映し「暫定 / 要列挙」を解消。Territory__c / ProposablePositionMulti__c は件数が多いため抜粋。OwnerId 等の ID 値・フィールド増減の網羅確認は次回の週末リフレッシュ（routine ④）で実施）。
>
> **週途中の鮮度フォールバック（重要）：** タスク中に選択肢値やフィールドが本ファイルと食い違って操作が失敗したら、**その1オブジェクトだけ** `salesforce_describe_object` でライブ確認して進める（本ファイル全体の再取得はしない）。差分は次回の週末リフレッシュが回収する。

---

## 全体概要

- **組織規模の目安：** Account 約14,000件。うち**クライアント企業は約170件**（`Contract_Status__c = '締結済み'`）、残り大多数は**候補者の在籍企業（職歴の会社）**。Account は企業マスターとクライアントが二重利用される。
- **正本の対応：** 候補者は Notion/Craft が正本、SF は CRM。`Notion_Page_ID__c` で相互参照。
- **接続方式：** SF はコネクタ一覧に出ず、`.mcp.json` 経由の project スコープ MCP。環境変数 `SALESFORCE_USERNAME` / `SALESFORCE_PASSWORD` / `SALESFORCE_TOKEN` / `SALESFORCE_INSTANCE_URL` の4つが要る。

## オブジェクト一覧（業務で使う主なもの）

既定項目セット（取得するフィールド）は **`agents/salesforce.md` の該当セクション**を参照。ここでは対応と用途のみ。

| オブジェクト | 用途 | 既定項目セット |
|---|---|---|
| `Contact` | 候補者／担当者 | salesforce.md ① |
| `Account` | 取引先＝企業（クライアント＋在籍企業） | salesforce.md ② |
| `Opportunity` | 案件＝ポジション | salesforce.md ③ |
| `matching__c` | パイプライン＝選考進捗（候補者×案件） | salesforce.md ④ |
| `Employment_History__c` | 在籍履歴 | 必要時に都度 describe |

※ `slackv2__*` 等は連携・設定用で業務データではない（リフレッシュ対象外）。

## 追跡対象オブジェクト（リフレッシュのスコープ）

週末リフレッシュ（routine ④）が describe する範囲。org 全体は重いのでここで絞る。

- **カスタムオブジェクト：** `QualifiedApiName LIKE '%__c'` を初回リフレッシュで自動列挙（既知：`matching__c` / `Employment_History__c`。他があれば自動追記）。
- **標準オブジェクト allowlist：** `Account` / `Contact` / `Opportunity` / `User`。

---

## 選択肢（picklist）値カタログ ← 本ファイルの主目的

WHERE 句や絞り込みで使う選択肢の正規値。**ここが describe で一番ドリフトする部分**なので週次で最新化する。
（下記は **2026-06-10 のライブ describe で確定済み**。Territory__c / ProposablePositionMulti__c のみ件数が多く抜粋。）

### Account.`Contract_Status__c`（契約状況／クライアント判定の鍵）
- `締結済み` … **= クライアント**（client-profile.md §2.4）
- `未締結` / `締結対応中` / `終了` / null … 非クライアント（候補者の在籍企業など）

### matching__c.`phase__c`（選考フェーズ・必須）
- **アクティブ判定 = `NOT IN ('脱落','入社済み')`**
- 全選択肢（順）：`スカウトメール送付済み` / `返信あり、P&L面談日程調整` / `P&L面談` / `応募意志確認` / `書類選考` / `カジュアル面談` / `1次面接` / `SPI/技術テスト` / `2次面接` / `最終面接` / `オファー面談` / `内定承諾/退職交渉・引き継ぎ` / `入社済み` / `脱落`

### matching__c.`DropReason__c`（脱落理由）
- `辞退` / `お見送り` / `応答なし` / `リリース`

### matching__c.`retainerphase__c`（リテーナー状況）
- `ーーー` / `提案中：先方検討中` / `失注：受注ならず` / `リテーナー対応中` / `成功：候補者決定` / `失敗：候補者決定ならず` / `その他`

### Opportunity.`StageName`（案件フェーズ・必須）
- `open` / `クローズ(P&L決定)` / `クローズ(他社経由)` / `クローズ(その他事由)` / `応答なし` / `資料請求` / `リード｜ウェビナー` / `商談調整中` / `商談決定` / `商談済` / `クローズした不成立取引` / `成約`

### Opportunity.`Jobtype__c`（職種）
- `CxO` / `営業` / `マーケティング` / `人事` / `コーポレート` / `デザイナー` / `エンジニア` / `データサイエンティスト` / `コンサル` / `PdM` / `PMM` / `PjM` / `カスタマーサクセス` / `事業開発` / `経営企画` / `ファイナンス`

### Opportunity.`priority__c` / `typeofcontract__c`
- priority__c：`高` / `中` / `低`
- typeofcontract__c：`通常Fee` / `UpFee` / `リテーナー`

### Contact 主要 picklist
- `Source__c`（ソース）：`LinkedIn` / `ビズリーチ` / `リクルートダイレクトスカウト` / `eight` / `日経転職版` / `リファラル` / `Facebook` / `X` / `YouTrust` / `doda X` / `ミドルの転職` / `OpenWork` / `Pitta` / `Liiga` / `SNS` / `その他`
- `reply__c`（スカウト返信）：`返信無し` / `返信有り`
- `English__c`：`ネイティブレベル` / `ビジネス会話レベル` / `日常会話レベル` / `基礎会話レベル`
- `FinalEducationList__c`（最終学歴）：`大学院卒(博士)以上` / `大学院卒(MBA)以上` / `大学院卒(修士)以上` / `大学卒以上` / `高専・専門・短大卒以上` / `高校卒業以上` / `その他`
- `Position__c`（役職＝職種大分類）：`経営・管理・人事` / `営業・サービス` / `マーケ・広告` / `IT・ゲーム・デザイン` / `コンサルタント・専門職` / `金融` / `メディカル`
- `CurrentSalary__c`（現年収レンジ）：`500万円未満` / `500-600万円` / `600-750万円` / `750-1,000万円` / `1,000-1,250万円` / `1,250-1,500万円` / `1,500-2,000万円` / `2,000-3,000万円` / `3,000-5,000万円` / `5,000万円以上`
- `RemoteRequirement__c` / `SideWorkRequirement__c`：`有` / `無` / `どちらでも可`
- `CarefulPerson__c`（要注意人物・複数選択）：`犯罪歴有` / `鬱履歴有` / `性格難` / `体調不良` / `その他`
- `Territory__c`（役職ポジション）・`ProposablePositionMulti__c`（提案可能ポジション）：**選択肢が各100件前後と多いため全列挙しない**。CEO/COO/CFO/CTO/CHRO/CISO/PdM/事業開発… 等の職位・職種が網羅されている。WHERE で個別値が要るときは `salesforce_describe_object` でその場確認する。

---

## よく使う SOQL（正本）

SOQL は `SELECT *` を持たないので必ず項目を名指しする。既定項目セットは salesforce.md を参照。

- **締結済みクライアント一覧：**
  `SELECT Id, Name, Contract_Status__c, NumberOfEmployees, Notion_Page_ID__c FROM Account WHERE Contract_Status__c = '締結済み'`
- **佐藤所有・アクティブなパイプライン：**
  `SELECT Id, Name, Contact__r.Name, Opportunity__r.Name, phase__c, LastModifiedDate FROM matching__c WHERE OwnerId = '0055h000004V2dtAAC' AND phase__c NOT IN ('脱落','入社済み')`
- **佐藤 雄太 OwnerId：** `0055h000004V2dtAAC`（sato-y@pnl.co.jp）

---

## 設計上の重要な特徴

1. **Account の二重利用：** 企業マスター（候補者の在籍企業 約14,000件）とクライアント（約170件）が同居。クライアントは必ず `Contract_Status__c = '締結済み'` で絞る（client-profile.md §2.4）。
2. **candidate は SF パイプライン所有者で判定：** 佐藤担当は `matching__c.OwnerId = '0055h000004V2dtAAC'` が正本。Notion パイプライン DB は enrichment 併用に留め、対象集合は SF に交差させる。
3. **横断キー：** `Notion_Page_ID__c`（Account/Contact/Opportunity/matching__c に存在）が Notion との突合キー。`matching__c` は `Contact__r` / `Opportunity__r` / `ApplyCompany__r` で関連オブジェクトを参照。
