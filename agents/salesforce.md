# agents/salesforce.md — Salesforce 読み込みエージェント

Salesforce（CRM）をMCP（`mcp__salesforce__*`）経由で読み込むときの共通ルール。
**トークン効率のため、各オブジェクトは「既定の項目セット」だけを取得する**（全項目取得は禁止。SOQLは
`SELECT *` を持たず必ず項目を名指しする方式なので、ここで定めた既定セットを使う）。
長文の自由記述（面談メモ・サマリー・各種メモ）は既定から外し、**ユーザーが明示的に求めたときだけ**追加取得する。

## 共通の方針

- `salesforce_query_records` を使うときは `fields` に**下記の既定セット**を渡す。`limit` を必ず付け、
  必要件数だけ取る。鮮度判断のため `LastModifiedDate` を既定に含めている。
- 「面談メモも見たい」「メモ欄も」「サマリーを」等と言われたら、その対象の**長文項目だけ**を既定セットに足して取得する。
- 全項目が必要な特殊ケース（オブジェクト構造の棚卸し等）は、ユーザーの明示要求があるときに限る。
- 主オブジェクトと正本の対応：候補者＝Notion/Craft が正本、Salesforce はCRM。`Notion_Page_ID__c` で相互参照できる。

### 例外：プロファイル初回生成・同期更新は長文も取得する（重要）

既定セットの「長文を弾く」方針は、**名前が出たときのざっと参照・マッチング走査・進捗確認**などルーティン用途のもの。
これに対し、候補者／クライアントプロファイル md の**初回生成**（CLAUDE.md §7/§8 の初回生成モード）と、
**面談メモ・選考理由・サマリー等を反映する同期更新**は、長文そのものが集約対象の一次情報になる。
**この2工程では既定セットに加えて長文項目も取得する**（弾かない）。

- Contact：`InterviewMemo__c`（面談メモ）, `Summary__c`（候補者サマリー）, `Tenshokujiku__c`（転職軸）,
  `Competitors__c`（他社選考状況）, `pros_cons__c`, `memo__c`, `Description__c` を追加。
- matching__c：`PLmemo__c`（メモ）, `DropReasonDetail__c`（脱落理由詳細）, `others__c`（申し送り）を追加。
- Account：プロファイル生成で背景が要るときは `Description`, `contract_memo__c` 等を必要に応じ追加。

判断基準：**「深く一度きり取り込む」工程＝長文込み／「軽く何度も見る」工程＝既定セットのみ**。

## オブジェクト一覧（業務で使う主なもの）

- `Contact`（候補者/担当者）
- `Account`（取引先＝企業）
- `Opportunity`（案件＝ポジション）
- `matching__c`（パイプライン＝選考進捗）
- `Employment_History__c`（在籍履歴。必要時に都度 describe して使う）
- ※ `slackv2__*` 等は連携・設定用で業務データではない。

---

## ① Contact（候補者/担当者）— 既定セット

```
Id, Name, furigana_lastname__c, furigana_firstname__c,
CurrentPosition__c, companycopy__c, Email, MobilePhone,
Age__c, CurrentSalary__c, ActualSalary__c, TargetSalary__c, ActualTargetSalary__c,
Territory__c, Position__c, ProposablePositionMulti__c,
Source__c, reply__c, active__c, English__c, FinalEducationList__c,
RemoteRequirement__c, SideWorkRequirement__c,
LinkedIn__c, badalert__c, CarefulPerson__c,
LastModifiedDate, Notion_Page_ID__c, Craft_Profile_URL_SY__c
```

| API名 | 項目名 |
|---|---|
| Id | 担当者/候補者 ID |
| Name | 氏名 |
| furigana_lastname__c / furigana_firstname__c | 姓・名（ふりがな） |
| CurrentPosition__c | 現職ポジション |
| companycopy__c | 所属企業（テキスト） |
| Email / MobilePhone | メール・携帯電話 |
| Age__c | 年齢 |
| CurrentSalary__c / ActualSalary__c | 現年収（選択／実数） |
| TargetSalary__c / ActualTargetSalary__c | 希望年収（選択／実数） |
| Territory__c | 役職（ポジション） |
| Position__c | 役職（職種） |
| ProposablePositionMulti__c | 提案可能ポジション |
| Source__c | ソース（媒体） |
| reply__c | スカウト返信有/無 |
| active__c | 転職活動アクティブ |
| English__c | 英語 |
| FinalEducationList__c | 最終学歴（選択） |
| RemoteRequirement__c / SideWorkRequirement__c | リモート希望・副業希望 |
| LinkedIn__c | LinkedIn |
| badalert__c / CarefulPerson__c | 要注意チェック・要注意人物 |
| LastModifiedDate | 最終更新日（鮮度） |
| Notion_Page_ID__c | Notion Page ID（※別コンサルの Notion を指す場合あり。佐藤の突合には使わない） |
| Craft_Profile_URL_SY__c | 佐藤の Craft プロファイルへのポインタ（非空＝作成済み。重複防止の主キー） |

**既定から外す長文（要求時のみ追加）：** `InterviewMemo__c`（面談メモ）, `Summary__c`（候補者サマリー）,
`memo__c`, `pros_cons__c`, `Tenshokujiku__c`（転職軸）, `Competitors__c`（他社選考状況）, `Description__c`。

---

## ② Account（取引先＝企業）— 既定セット

```
Id, Name, furigana__c, NumberOfEmployees, Contract_Status__c,
LastModifiedDate, Notion_Page_ID__c, Craft_Profile_URL_SY__c
```

| API名 | 項目名 |
|---|---|
| Id | 取引先 ID |
| Name | 取引先名 |
| furigana__c | 会社名（ふりがな） |
| NumberOfEmployees | 従業員数 |
| Contract_Status__c | 契約状況 |
| LastModifiedDate | 最終更新日（鮮度） |
| Notion_Page_ID__c | Notion Page ID（※別コンサルの Notion を指す場合あり。佐藤の突合には使わない） |
| Craft_Profile_URL_SY__c | 佐藤の Craft プロファイルへのポインタ（非空＝作成済み。重複防止の主キー） |

**既定から外す長文：** `Description`, `Invoice__c`, `contract_memo__c`, `contactmemo__c`,
`Field3__c`, `Contract_Summary_Text__c`。
※ 業種・Webサイト・契約期間・ティア・開拓状況・同席可否・財務（証券コード/時価総額/売上高）等は
既定から除外（必要時に項目名を指定して追加取得）。

---

## ③ Opportunity（案件＝ポジション）— 既定セット

```
Id, Name, Account.Name, StageName, Jobtype__c, priority__c,
Amount, incomelimit__c, rate__c, typeofcontract__c, URL__c,
LastModifiedDate, Notion_Page_ID__c
```

| API名 | 項目名 |
|---|---|
| Id | 案件 ID |
| Name | 案件名 |
| Account.Name | 取引先名（親企業・参照） |
| StageName | フェーズ |
| Jobtype__c | 職種 |
| priority__c | 優先度 |
| Amount | 金額 |
| incomelimit__c | 年収上限 |
| rate__c | 料率(%) |
| typeofcontract__c | 契約形態 |
| URL__c | 一般公開URL |
| LastModifiedDate | 最終更新日（鮮度） |
| Notion_Page_ID__c | Notion Page ID |

**既定から外す長文：** `Description`, `information__c`, `memo__c`。
※ 完了予定日・完了/成立フラグ・ポートフォリオ等は既定から除外（必要時に追加取得）。

---

## ④ matching__c（パイプライン＝選考進捗）— 既定セット

```
Id, Name, Contact__r.Name, ApplyCompany__r.Name, Opportunity__r.Name,
phase__c, DropReason__c, money__c, Kakuteikingaku__c, JoinYYMMDD__c,
currently_position__c, annualIncome__c, retainer__c, retainerphase__c,
LastModifiedDate, Notion_Page_ID__c
```

| API名 | 項目名 |
|---|---|
| Id | カスタムオブジェクト ID |
| Name | マッチング名 |
| Contact__r.Name | 候補者氏名（参照先） |
| ApplyCompany__r.Name | 選考企業名（参照先） |
| Opportunity__r.Name | 案件名（参照先） |
| phase__c | フェーズ |
| DropReason__c | 脱落理由 |
| money__c / Kakuteikingaku__c | 想定売上金額・確定売上金額 |
| JoinYYMMDD__c | 入社年月日 |
| currently_position__c | 役職 |
| annualIncome__c | 理論年収 or リテーナー請求金額 |
| retainer__c / retainerphase__c | リテーナー決定候補者・リテーナー状況 |
| LastModifiedDate | 最終更新日（鮮度） |
| Notion_Page_ID__c | Notion Page ID |

**既定から外す：** `PLmemo__c`, `others__c`, `DropReasonDetail__c` の長文、
ステージ通過の真偽フラグ群（`X3_P_L__c` 等）・段階別の通過日付（`X02_03__c` 等）。
選考の細かい履歴・日付遷移を追うときだけ、必要なものを指定して追加取得する。
