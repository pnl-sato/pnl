# Salesforce 構造ドキュメント（sf_structure.md）

> **役割の分担（重要）：** SF の「**読み方＝オブジェクト別の既定項目セット**」は `agents/salesforce.md` が正本。本ファイルはそれを補完する**スキーマ参照**で、describe が一番効く部分＝**選択肢（picklist）値のカタログ**・**よく使う SOQL**・**オブジェクト/フィールドの増減**を週次で最新化して持つ。フィールド名の一覧表は salesforce.md と重複させない（あちらを参照）。SF を読むタスクは `agents/salesforce.md`、WHERE 句の選択肢値や SOQL の組み立て・スキーマ変更の確認はこのファイル、と使い分ける。
>
> **更新方式：** `agents/routines.md`「④ 週末SF構造リフレッシュ」が**週1回**、対象オブジェクトを `salesforce_describe_object` で取得→**選択肢値・フィールド増減だけ蒸留**→**差分があるときだけ `automation/sf-structure-refresh` ブランチで PR を開き、佐藤が diff をレビューして手動マージ**する（スキーマ変化を確認してから入れるため PR 経由。GitHub プロキシは push を checkout 中のブランチに限定する点に注意）。重い describe を週1セッションに閉じ込め、平日タスクはこの蒸留版を読むだけにする（CLAUDE.md §13）。
>
> **最終更新：** 2026-06-21（ライブ describe で全追跡オブジェクト（Account / Contact / Opportunity / matching__c / Employment_History__c / CompanyNews__c / User）を再取得。新規カスタムオブジェクト `CompanyNews__c`（企業ニュース）を検出・追加。既存オブジェクトの picklist 値・フィールドに変更なし。agents/salesforce.md の既定項目セット全フィールドの実在を確認済み（消えた／改名されたフィールドなし）。Territory__c / ProposablePositionMulti__c は各 100〜130件超のため引き続き全列挙しない（on-demand describe で確認）。）
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
| `CompanyNews__c` | 企業ニュース（Account に紐づく記事・プレスリリース等） | 必要時に都度 describe |

※ `slackv2__*` 等は連携・設定用で業務データではない（リフレッシュ対象外）。

## 追跡対象オブジェクト（リフレッシュのスコープ）

週末リフレッシュ（routine ④）が describe する範囲。org 全体は重いのでここで絞る。

- **カスタムオブジェクト：** `QualifiedApiName LIKE '%__c'` で列挙済み（2026-06-21確認）。業務データを持つカスタムオブジェクト：`matching__c`（パイプライン）・`Employment_History__c`（在籍履歴）・`CompanyNews__c`（企業ニュース、2026-06-21新規検出）・`In_App_Checklist_Settings__c`（設定用、業務データなし）。`slackv2__*` 系（11件）は Slack 連携・設定用でリフレッシュ対象外。
- **標準オブジェクト allowlist：** `Account` / `Contact` / `Opportunity` / `User`。

---

## 選択肢（picklist）値カタログ ← 本ファイルの主目的

WHERE 句や絞り込みで使う選択肢の正規値。**ここが describe で一番ドリフトする部分**なので週次で最新化する。
（下記は **2026-06-14 のライブ describe で確定済み**。Territory__c / ProposablePositionMulti__c のみ件数が多く引き続き抜粋。）

---

### Account の picklist

#### `Contract_Status__c`（契約状況／クライアント判定の鍵）
- `締結済み` … **= クライアント**（client-profile.md §2.4）
- `未締結` / `締結対応中` / `終了` / null … 非クライアント（候補者の在籍企業など）

#### `contract_autoupdate__c`（契約自動更新）
- `有` / `無`

#### `InterviewAvailable__c`（面談/面接同席可否）
- `未確認` / `OK` / `NG`

#### `Weekare_approach_method__c`（W_アプローチ手法）
- `メール` / `フォーム` / `電話` / `営業代行`

#### `Weekare_textlimit__c`（W_文字数制限）
- `≦100` / `≦200` / `≦300` / `≦400` / `≦500` / `≦600` / `≦700` / `≦800` / `≦900` / `≦1000` / `1001≦`

#### `Field1__c`（連絡方法 ※multipicklist）
- `ーーー` / `Slack` / `メール` / `電話` / `Facebook` / `LinkedIn` / `LINE` / `その他`

#### `Field2__c`（新規クライアント開拓状況）
- `ーーー` / `アタック検討` / `アタック注力しない` / `アタック中` / `アタック済` / `先方提案検討中` / `受注` / `失注` / `その他`

#### `development_Tier__c`（ティア）
- `1軍（強いルアー）` / `2軍（普通ルアーまたはリテーナー候補）`

#### `list_source__c`（リスト元）
- `Resumee` / `四季報`

---

### matching__c の picklist

#### `phase__c`（選考フェーズ・必須）
- **アクティブ判定 = `NOT IN ('脱落','入社済み')`**
- 全選択肢（順）：`スカウトメール送付済み` / `返信あり、P&L面談日程調整` / `P&L面談` / `応募意志確認` / `書類選考` / `カジュアル面談` / `1次面接` / `SPI/技術テスト` / `2次面接` / `最終面接` / `オファー面談` / `内定承諾/退職交渉・引き継ぎ` / `入社済み` / `脱落`

#### `DropReason__c`（脱落理由）
- `辞退` / `お見送り` / `応答なし` / `リリース`

#### Notion 選考状況 ↔ matching__c マッピング（パイプライン二重更新用）
Notion パイプライン（`選考状況` status ＋ `リリース理由` select）を更新したら、SF `matching__c` も必ず揃える（candidate-profile.md §6・2026-06 佐藤指示）。対応は以下。
- 進行中フェーズ（`カジュアル面談`/`1次面接`/`書類選考`…）→ `phase__c` の同名フェーズ。面談日等は対応する日付項目（`X05_06__c` 等）へ。
- Notion `選考状況=リリース` ＋ `リリース理由`（`保留`/`応答なし`/`辞退`/`お見送り`）→ `phase__c=脱落` ＋ `DropReason__c`（**`保留`→`リリース`**、他は同名）＋ `DropReasonDetail__c` に経緯（必要なら `DropReasonSummary__c` に一行要約）＋ `X01__c`（00→脱落の日付）に脱落日。
- Notion `入社` → `phase__c=入社済み`。
- 横断キーは `Notion_Page_ID__c`＝Notion パイプラインページID。未設定なら更新時に併せてセットする。

#### `retainerphase__c`（リテーナー状況）
- `ーーー` / `提案中：先方検討中` / `失注：受注ならず` / `リテーナー対応中` / `成功：候補者決定` / `失敗：候補者決定ならず` / `その他`

#### `AttendAnInterviewWith__c`（面接同席有/無）
- `面接同席【未実施】` / `面接同席【実施済】`

#### `Field4__c`（スカウト媒体入社報告）
- `入社報告【未】` / `入社報告【済】` / `入社報告【不要】`

#### `salesreport__c`（P&L決定報告）
- `報告済み`

#### `BillingDetails__c`（請求内容）
- `採用コンサルティングフィー（決定者入社時請求）` / `リテーナーサーチ着手金（契約書締結後請求）` / `その他`

#### `HowToSendInvoices__c`（請求書送付方法）
- `郵送（原本送付）` / `メール（データ送信）`

#### `quotation__c`（見積書送付の有無）
- `必要`

#### `Field1__c`（請求書作成状況）
- `請求書作成済`

#### `Field2__c`（担当者請求書確認状況）
- `担当者請求書確認済`

---

### Opportunity の picklist

#### `StageName`（案件フェーズ・必須）
- `open` / `クローズ(P&L決定)` / `クローズ(他社経由)` / `クローズ(その他事由)` / `応答なし` / `資料請求` / `リード｜ウェビナー` / `商談調整中` / `商談決定` / `商談済` / `クローズした不成立取引` / `成約`

#### `Jobtype__c`（職種）
- `CxO` / `営業` / `マーケティング` / `人事` / `コーポレート` / `デザイナー` / `エンジニア` / `データサイエンティスト` / `コンサル` / `PdM` / `PMM` / `PjM` / `カスタマーサクセス` / `事業開発` / `経営企画` / `ファイナンス`

#### `priority__c` / `typeofcontract__c`
- priority__c：`高` / `中` / `低`
- typeofcontract__c：`通常Fee` / `UpFee` / `リテーナー`

#### `Loss_Reason__c`（失注理由）
- `Lost to Competitor` / `No Budget / Lost Funding` / `No Decision / Non-Responsive` / `Price` / `Other`

---

### Contact の picklist

#### `Source__c`（ソース）
- `LinkedIn` / `ビズリーチ` / `リクルートダイレクトスカウト` / `eight` / `日経転職版` / `リファラル` / `Facebook` / `X` / `YouTrust` / `doda X` / `ミドルの転職` / `OpenWork` / `Pitta` / `Liiga` / `SNS` / `その他`

#### `reply__c`（スカウト返信）
- `返信無し` / `返信有り`

#### `English__c`
- `ネイティブレベル` / `ビジネス会話レベル` / `日常会話レベル` / `基礎会話レベル`

#### `FinalEducationList__c`（最終学歴）
- `大学院卒(博士)以上` / `大学院卒(MBA)以上` / `大学院卒(修士)以上` / `大学卒以上` / `高専・専門・短大卒以上` / `高校卒業以上` / `その他`

#### `Position__c`（役職＝職種大分類）
- `経営・管理・人事` / `営業・サービス` / `マーケ・広告` / `IT・ゲーム・デザイン` / `コンサルタント・専門職` / `金融` / `メディカル`

#### `CurrentSalary__c`（現年収レンジ）
- `500万円未満` / `500-600万円` / `600-750万円` / `750-1,000万円` / `1,000-1,250万円` / `1,250-1,500万円` / `1,500-2,000万円` / `2,000-3,000万円` / `3,000-5,000万円` / `5,000万円以上`

#### `TargetSalary__c`（希望年収）
- `問わない` / `600万円` / `700万円` / `800万円` / `900万円` / `1,000万円` / `1,100万円` / `1,200万円` / `1,300万円` / `1,400万円` / `1,500万円` / `1,600万円` / `1,700万円` / `1,800万円` / `1,900万円` / `2,000万円` / `2,100万円` / `2,200万円` / `2,300万円` / `2,400万円` / `2,500万円` / `2,600万円` / `2,700万円` / `2,800万円` / `2,900万円` / `3,000万円` / `4,000万円` / `5,000万円以上`

#### `RemoteRequirement__c` / `SideWorkRequirement__c`
- `有` / `無` / `どちらでも可`

#### `CarefulPerson__c`（要注意人物・複数選択）
- `犯罪歴有` / `鬱履歴有` / `性格難` / `体調不良` / `その他`

#### `PersonalInformation1__c`（個人情報の取扱い同意）
- `なし` / `同意書サイン済` / `不要`

#### `W_source__c`（W_ソース）
- `営業代行` / `HRpro` / `日本の人事部` / `フォームマーケティング` / `問い合わせ・資料請求`

#### `Territory__c`（役職ポジション）・`ProposablePositionMulti__c`（提案可能ポジション）
**選択肢が各 100〜130件超と多いため全列挙しない**（2026-06-14 describe で件数確認済み）。CEO/COO/CFO/CTO/CHRO/CISO/PdM/事業開発… 等の職位・職種が網羅されている。WHERE で個別値が要るときは `salesforce_describe_object` でその場確認する。

---

### Employment_History__c の picklist

#### `Employment_Type__c`（雇用形態）
- `正社員` / `業務委託` / `役員` / `インターン` / `その他`

---

### CompanyNews__c の picklist（2026-06-21 新規追加）

#### `Category__c`（カテゴリ）
- `資金調達` / `IPO` / `M&A` / `上場廃止` / `TOB` / `MBO` / `経営統合` / `倒産` / `社長交代` / `業績修正` / `決算` / `新サービス` / `新機能` / `プレスリリース` / `CEO発信` / `note新着` / `ブログ` / `登壇` / `業務提携` / `マイルストーン`

**主要フィールド（参考）：** `Account__c`（Account への参照）・`URL__c`（記事URL、必須）・`Published_Date__c`（公開日）・`Posted_At__c`（Slack投稿日）・`Summary__c`（3行サマリー）・`Pipeline_Notes__c`（パイプライン影響）。

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
   - **注意：** `Notion_Page_ID__c` は**別コンサルの Notion を指す**ことがあり、佐藤の Notion／Craft への突合キーとしては使えない（2026-06 佐藤確認）。
4. **佐藤の Craft プロファイル突合キー（2026-06 追加）：** `Craft_Profile_URL_SY__c`（**Account・Contact** に新設、Text 255、FLS＝システム管理者のみ）。佐藤の業務エージェントが作成した Craft プロファイル（クライアント／候補者）への**ポインタ（rootBlockId／`craftdocs://` ディープリンク。公開共有リンクは入れない）**。**非空＝佐藤の Craft 作成済み**を意味する決定論的マーカーで、夜間 backfill（routines.md）の重複作成防止の**主キー**。`Notion_Page_ID__c`（他コンサル用）とは別管理。対象抽出は `... AND Craft_Profile_URL_SY__c = null`、作成後に rootBlockId を書き戻す（client-profile.md §2.5／candidate-profile.md §2.5・§5.2）。
