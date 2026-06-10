# Salesforce 構造ドキュメント（sf_structure.md）

> **役割の分担（重要）：** SF の「**読み方＝オブジェクト別の既定項目セット**」は `agents/salesforce.md` が正本。本ファイルはそれを補完する**スキーマ参照**で、describe が一番効く部分＝**選択肢（picklist）値のカタログ**・**よく使う SOQL**・**オブジェクト/フィールドの増減**を週次で最新化して持つ。フィールド名の一覧表は salesforce.md と重複させない（あちらを参照）。SF を読むタスクは `agents/salesforce.md`、WHERE 句の選択肢値や SOQL の組み立て・スキーマ変更の確認はこのファイル、と使い分ける。
>
> **更新方式：** `agents/routines.md`「④ 週末SF構造リフレッシュ」が**週1回**、対象オブジェクトを `salesforce_describe_object` で取得→**選択肢値・フィールド増減だけ蒸留**→**差分があるときだけ main を更新**する。重い describe を週1セッションに閉じ込め、平日タスクはこの蒸留版を読むだけにする（CLAUDE.md §13）。
>
> **最終更新：** （初回リフレッシュ未実行。本ファイルは CLAUDE.md / agents/salesforce.md / agents/routines.md / agents/client-profile.md で既出の事実だけで**暫定シード**したもの。`暫定` / `要列挙` と付した箇所は describe 未検証で、初回リフレッシュで確定する）
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

### Account.`Contract_Status__c`（契約状況／クライアント判定の鍵）
- `締結済み` … **= クライアント**（client-profile.md §2.4）
- `未締結` / `締結対応中` / `終了` / null … 非クライアント（候補者の在籍企業など）
- 〔選択肢の正確な API 値は **暫定**。describe 検証待ち〕

### matching__c.`phase__c`（選考フェーズ）
- **アクティブ判定 = `NOT IN ('脱落','入社済み')`**
- 既知の値：`脱落` / `入社済み`。**その他の段階値は 要列挙**（describe で確定）

### Opportunity.`StageName`（案件フェーズ）
- **要列挙**（describe で確定）

### その他 picklist（`Contact.active__c` / `reply__c` / `English__c` / `FinalEducationList__c` / `Territory__c` / `Position__c`、`Opportunity.Jobtype__c` / `priority__c` / `typeofcontract__c`、`matching__c.DropReason__c` / `retainerphase__c` 等）
- **要列挙**（初回リフレッシュで、各 picklist の選択肢値をここに展開する）

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
