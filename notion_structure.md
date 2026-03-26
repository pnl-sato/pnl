# Notion DB 構造ドキュメント

本ドキュメントは、P&L（Pole & Line）が運用するNotionワークスペースのデータベース構成を記録したものです。
Notionと連携するシステムを開発する際の参照資料として使用してください。

---

## 全体概要

このNotionは**採用エージェント業務（ヘッドハンティング）**を管理するために構築されています。
クライアント企業から依頼されたポジションに対し、候補者をマッチングし、選考プロセスを追跡するシステムです。

```
企業（クライアント／在籍企業）
  └── ポジション（案件）
        └── パイプライン（候補者 × ポジションの選考エントリ）
              ├── 候補者
              ├── スカウトDB（スカウト文）
              ├── 面談メモ
              └── ToDo
```

---

## データベース一覧

### 1. 企業 DB
**Collection ID:** `collection://1fb7d017-b6a0-80b4-a83a-000b901f891a`

クライアント企業と候補者の在籍企業を **一つのDBで兼用** している。`カテゴリ` フィールドで区別する。

| プロパティ名 | 型 | 説明 |
|---|---|---|
| 企業名 | title | 企業名 |
| カテゴリ | select | `在籍企業` / `クライアント` |
| コード | text | 企業コード（略称） |
| 上場／非上場 | select | `上場（東証プライム）` / `上場（グロース）` / `非上場` |
| 従業員規模 | select | `〜50名` / `50〜200名` / ... / `5000名以上` |
| 事業ドメイン | multi_select | 教育・ヘルスケア・金融・製造・サイバーセキュリティ など |
| 事業モデル | multi_select | SaaS・プラットフォーム・AI・データ基盤・コンサルティング など |
| P&Lとしての関わり度合い | select | `高` / `中` / `低` |
| SalesForce | url | SalesForce レコードURL |
| ATS | url | ATS リンク |
| ChatGPT | url | ChatGPT スレッドURL |
| NoteBookLM | url | NotebookLM URL |
| Googleドキュメントリスト化 | checkbox | - |
| サイバーセキュリティ関連企業 | checkbox | - |

**リレーション:**
- ポジション → `collection://1fb7d017-b6a0-8052-a7c9-000b1aa76cda`
- パイプライン → `collection://20f7d017-b6a0-807c-a60f-000b827c6841`
- 候補者 → `collection://2057d017-b6a0-80fc-9e8d-000b3b6ab37e`
- 打ち合わせメモ → `collection://20c7d017-b6a0-8014-82f5-000b750ec0a8`
- ToDo → `collection://2257d017-b6a0-8026-867c-000bb0969507`
- セキュリティ人材 → `collection://2cd7d017-b6a0-800a-8333-000b3573c929`

---

### 2. ポジション DB
**Collection ID:** `collection://1fb7d017-b6a0-8052-a7c9-000b1aa76cda`

クライアントから受注した採用案件（求人ポジション）を管理する。

| プロパティ名 | 型 | 説明 |
|---|---|---|
| 名前 | title | ポジション名 |
| ステータス | status | `オープン` / `クローズ` |
| 職種 | multi_select | BizDev・セキュリティ・PdM・COO／経営企画・事業企画・営業・エンジニアリング・人事／HR・コーポレート・マーケティング・コーポレートIT |
| 職位 | multi_select | CxO・役員・部長・マネージャー・シニア・メンバー |
| 契約形態 | select | `リテーナー` / `UpFee` / `通常` |
| 料率 | number (%) | 成功報酬率 |
| 年収（下限） | number (¥) | - |
| 年収（上限） | number (¥) | - |
| 決定確度 | select | `S` / `A` / `B` / `C` / `D` |
| 注力 | checkbox | 注力案件フラグ |
| フック強度 | select | `強` / `中` / `弱` |
| フェーズ適性 | multi_select | 0→1・1→10・10→100・100→1000・大企業運営・再建/Turnaround |
| 役割タイプ | multi_select | Builder・Scaler・Operator・Fixer・Specialist |
| 想定返信率 | number (%) | - |
| 報酬-補足 | text | - |
| 初回ヒアリング／登録日 | date | - |
| ATS | url | ATSリンク |
| Sec-カテゴリ | multi_select | CISO/セキュリティ責任者・セキュリティマネージャー・AppSec・インフラ/クラウドセキュリティ など（セキュリティ職種専用） |
| Sec-守備範囲 | multi_select | ガバナンス・データ保護・Threat Modeling・クラウド・AppSec・IAM・EDR/SIEM・DevSecOps など |
| Sec-役割レベル | select | Head/CISO候補・Manager・Lead・Senior・Member |
| Sec-採用背景 | text | セキュリティ職種専用メモ |

**計算フィールド（formula）:**
- `報酬レンジ` : 年収下限〜上限の表示
- `売上予測` : 料率から計算
- `案件スコア` : 複数指標の総合スコア
- `返信率` : スカウト送信数に対する返信率
- `テンプレート名` / `ChatGPTスレッド名` : 自動生成

**ロールアップ:**
- `コード` / `クライアント名` / `事業ドメイン` / `事業モデル` / `上場/非上場` / `従業員規模` (→ 企業DBから)
- `スカウト媒体` / `送信数（合計）` / `返信数（合計）` (→ スカウトDBから)
- `書類選考以降（件）` / `Intro→Casual:days` / `P&L密接度` (→ パイプラインから)

**リレーション:**
- クライアント → 企業DB (`collection://1fb7d017-b6a0-80b4-a83a-000b901f891a`)
- パイプライン → `collection://20f7d017-b6a0-807c-a60f-000b827c6841`
- スカウト文 → `collection://2597d017-b6a0-801b-8185-000ba4b9661e`
- メモ → `collection://20c7d017-b6a0-8014-82f5-000b750ec0a8`
- ToDo → `collection://2257d017-b6a0-8026-867c-000bb0969507`

---

### 3. パイプライン DB
**Collection ID:** `collection://20f7d017-b6a0-807c-a60f-000b827c6841`

候補者 × ポジションの組み合わせで1レコード。選考プロセスの進捗を管理する。

| プロパティ名 | 型 | 説明 |
|---|---|---|
| 名前 | title | パイプライン名（自動生成） |
| 選考状況 | status | 下記参照 |
| 紹介日 | date | 推薦した日付 |
| P&L面談 | date | P&L担当者との面談日 |
| カジュアル面談 | date | - |
| 書類選考 | date | - |
| 1次面接 | date | - |
| 2次面接 | date | - |
| 3次面接 | date | - |
| 最終面接 | date | - |
| オファー面談 | date | - |
| リリース理由 | select | `応答なし` / `辞退` / `お見送り` / `保留` |
| リリース理由詳細 | text | - |
| 推薦理由 | text | - |
| 推薦状 | url | 推薦状ドキュメントURL |
| 前日リマインド作成 | checkbox | - |
| ID | auto_increment_id | 自動採番 |

**選考状況ステータス詳細:**

| ステータス | グループ |
|---|---|
| スカウト連絡済 | To-do |
| P&面談調整中 | To-do |
| P&L面談 | To-do |
| 応募意思確認 | To-do |
| 書類選考 | In progress |
| カジュアル面談 | In progress |
| 1次面接 | In progress |
| 2次面接 | In progress |
| 3次面接 | In progress |
| 最終面接 | In progress |
| オファー面談 | In progress |
| 内定受諾／退職プロセス | In progress |
| 入社 | Complete |
| お見送り | Complete |
| リリース | Complete |
| スカウト辞退 | Complete |

**計算フィールド（formula）:**
- `LT_Total` / `LT_Intro_Doc` / `LT_Doc_Next` / `LT_Casual_1st` / `LT_1st_2nd` / `LT_2nd_3rd` / `LT_3rd_Final` / `LT_Final_Offer` : 各フェーズ間のリードタイム（日数）
- `次回日程` : 次のアクション日付
- `フォロー必要（3日経過）` : フォローアップが必要かどうか
- `売上予測` : 成約時の売上見込み
- `転職検討理由` / `推薦状：氏名` : 候補者情報からの計算

**ロールアップ:**
- `クライアント` / `コード` / `年齢` / `最低希望年収` / `案件料率` / `引用`

**リレーション:**
- 候補者 → `collection://2057d017-b6a0-80fc-9e8d-000b3b6ab37e`
- ポジション → `collection://1fb7d017-b6a0-8052-a7c9-000b1aa76cda`
- スカウトDB → `collection://2597d017-b6a0-801b-8185-000ba4b9661e`
- 面談メモ → `collection://20c7d017-b6a0-8014-82f5-000b750ec0a8`
- 選考評価 → `collection://2177d017-b6a0-806a-b025-000b6797112d`
- ToDo → `collection://2257d017-b6a0-8026-867c-000bb0969507`
- Google Drive ファイル → `collection://2817d017-b6a0-8061-9895-000be221f104`

---

### 4. 候補者 DB
**Collection ID:** `collection://2057d017-b6a0-80fc-9e8d-000b3b6ab37e`

転職候補者の基本プロフィールを管理する。

| プロパティ名 | 型 | 説明 |
|---|---|---|
| 名前 | title | 氏名 |
| 姓（ふりがな） | text | - |
| 名（ふりがな） | text | - |
| 生年月日 | date | - |
| 職種 | multi_select | BizDev・セキュリティ・PdM・COO/経営企画・事業企画・人事/HR・営業・エンジニアリング・マーケティング・コーポレート |
| 職位 | select | CxO・役員・部長・ディレクター・マネージャー・シニア・メンバー |
| スキル | multi_select | 採用（中途/新卒/エンジニア）・組織開発・新規事業開発・バックエンド開発・クラウドインフラ など多数 |
| 技術領域 | select | モバイルアプリ開発・AI/機械学習・通信基盤/WebRTC |
| 役割タイプ | multi_select | Specialist・Fixer・Operator・Scaler・Builder |
| フェーズ適性 | multi_select | 0→1・1→10・10→100・100→1000・大企業運営・再建/ターンアラウンド |
| 現年収 | number (¥) | - |
| 最低希望年収 | number (¥) | - |
| 転職検討理由 | text | - |
| 副業希望 | checkbox | - |
| ポジション | text | 現在の肩書 |
| SalesForce | url | SFレコードURL |
| NotebookLM | url | - |
| 生成AI | url | 生成AI用URL |

**計算フィールド:**
- `年齢` : 生年月日から計算
- `ふりがな` : 姓名ふりがなの結合
- `名前順` : ソート用

**リレーション:**
- 在籍企業 → 企業DB (`collection://1fb7d017-b6a0-80b4-a83a-000b901f891a`)
- パイプライン → `collection://20f7d017-b6a0-807c-a60f-000b827c6841`
- 面談メモ → `collection://20c7d017-b6a0-8014-82f5-000b750ec0a8`
- ToDo → `collection://2257d017-b6a0-8026-867c-000bb0969507`
- Google Drive ファイル → `collection://2817d017-b6a0-80d8-b224-000b89291adb`

---

### 5. スカウトDB
**Collection ID:** `collection://2597d017-b6a0-801b-8185-000ba4b9661e`

スカウトメッセージのテンプレートと送信実績を管理する。

| プロパティ名 | 型 | 説明 |
|---|---|---|
| 識別ID | title | スカウト文の識別ID |
| タイトル | text | スカウト文タイトル |
| DB | multi_select | `Linkedin` / `Bizreach` / `dodaX` / `eight` / `リクナビHRTech` / `SF（Owner）` / `SF（Others）` / `YOUTRUST` |
| Ver | number | バージョン番号 |
| 送信数 | number | 送信総数 |
| 返信数 | number | 返信総数 |
| 使用中 | checkbox | 現在使用中かどうか |
| 再送用 | checkbox | 再送用テンプレートかどうか |
| 使用開始日 | date | - |
| 備考 | text | - |

**計算フィールド:**
- `返信率` : 返信数/送信数
- `テンプレート名` : 自動生成
- `ポジション（グループ）` : ポジションのグループ名

**ロールアップ:**
- `クライアント` / `注力`

**リレーション:**
- ポジション → `collection://1fb7d017-b6a0-8052-a7c9-000b1aa76cda`
- パイプライン → `collection://20f7d017-b6a0-807c-a60f-000b827c6841`
- ToDo → `collection://2257d017-b6a0-8026-867c-000bb0969507`

---

### 6. 面談メモ DB
**Collection ID:** `collection://20c7d017-b6a0-8014-82f5-000b750ec0a8`

全ての面談・打ち合わせのメモを一元管理する。

| プロパティ名 | 型 | 説明 |
|---|---|---|
| 名前 | title | メモのタイトル |
| 日付 | date | 面談実施日 |
| カテゴリ | select | `打ち合わせ` / `候補者面談` / `面談／面接同席` / `説明会` |
| ID | auto_increment_id | 自動採番 |

**計算フィールド:**
- `名前候補` : 自動生成タイトル候補
- `実施日` : 日付のフォーマット
- `PDFName` : PDF出力用ファイル名

**ロールアップ:**
- `コード` / `P&L面談`

**リレーション:**
- 企業 → `collection://1fb7d017-b6a0-80b4-a83a-000b901f891a`
- ポジション → `collection://1fb7d017-b6a0-8052-a7c9-000b1aa76cda`
- 候補者 → `collection://2057d017-b6a0-80fc-9e8d-000b3b6ab37e`
- パイプライン → `collection://20f7d017-b6a0-807c-a60f-000b827c6841`

---

### 7. ToDo DB
**Collection ID:** `collection://2257d017-b6a0-8026-867c-000bb0969507`

タスク管理DB。GTD（Getting Things Done）メソッドに基づく構成。

| プロパティ名 | 型 | 説明 |
|---|---|---|
| Title | title | タスク名 |
| ステータス | status | `未着手` / `進行中` / `完了` |
| TaskType | select | `Inbox 📨` / `NextAction 🚀` / `Waiting ⏳` / `Project 🗂️` / `Someday 💭` |
| Category | select | `Pole&Line` / `Private` / `Personal Trainer` / `マンション理事会` |
| 優先度 | select | `高` / `中` / `低` |
| Ower / 1on1 | select | `Owner` / `1on1` |
| assigned to | person | 担当者 |
| 説明 | text | タスクの詳細説明 |
| 開始時刻 | date | - |
| 完了日時 | date | - |
| スカウトToDo | checkbox | スカウト関連タスクフラグ |
| Todoist | url | TodoistリンクURL |
| webcliper | url | Webクリッパーリンク |
| 親ToDo | relation (self) | 親タスク |
| 子ToDo | relation (self) | 子タスク |

**計算フィールド:**
- `見積もり時間` : タスクの見積時間
- `リンク表示用日時` : 表示用日時フォーマット

**ロールアップ:**
- `見積もり時間（子タスク）`

**リレーション（業務オブジェクトとの紐付け）:**
- クライアント → `collection://1fb7d017-b6a0-80b4-a83a-000b901f891a`
- ポジション → `collection://1fb7d017-b6a0-8052-a7c9-000b1aa76cda`
- パイプライン → `collection://20f7d017-b6a0-807c-a60f-000b827c6841`
- 候補者 → `collection://2057d017-b6a0-80fc-9e8d-000b3b6ab37e`
- スカウト文 → `collection://2597d017-b6a0-801b-8185-000ba4b9661e`

---

## DB間のリレーション図

```
                    ┌──────────┐
                    │  企業DB  │
                    │（クライア│
                    │ント/在籍）│
                    └────┬─────┘
                         │
          ┌──────────────┼──────────────────┐
          │              │                  │
     ┌────▼─────┐   ┌────▼──────┐   ┌──────▼──────┐
     │ポジション│   │ 候補者DB  │   │  面談メモDB  │
     │   DB     │   │           │   │（打ち合わせ）│
     └────┬─────┘   └────┬──────┘   └─────────────┘
          │              │
          └──────┬────────┘
                 │
          ┌──────▼──────┐
          │ パイプライン │◄──── スカウトDB
          │    DB        │
          └──────┬───────┘
                 │
                 ├── 面談メモDB（選考同席）
                 ├── 選考評価DB
                 └── ToDoDB
```

---

## Collection ID 早見表

| DB名 | Collection ID |
|------|--------------|
| 企業 | `collection://1fb7d017-b6a0-80b4-a83a-000b901f891a` |
| ポジション | `collection://1fb7d017-b6a0-8052-a7c9-000b1aa76cda` |
| パイプライン | `collection://20f7d017-b6a0-807c-a60f-000b827c6841` |
| 候補者 | `collection://2057d017-b6a0-80fc-9e8d-000b3b6ab37e` |
| スカウトDB | `collection://2597d017-b6a0-801b-8185-000ba4b9661e` |
| 面談メモ | `collection://20c7d017-b6a0-8014-82f5-000b750ec0a8` |
| ToDo | `collection://2257d017-b6a0-8026-867c-000bb0969507` |
| 選考評価 | `collection://2177d017-b6a0-806a-b025-000b6797112d` |
| セキュリティ人材 | `collection://2cd7d017-b6a0-800a-8333-000b3573c929` |

---

## 設計上の重要な特徴

### 1. 企業DBの二重利用
`カテゴリ` フィールドで「在籍企業」と「クライアント」を同一DBで管理している。
新しいクライアント企業に転職した候補者の在籍企業がそのままクライアントになり得るため、
重複登録を避ける設計になっている。

### 2. パイプラインは候補者×ポジションの交差テーブル
1人の候補者が複数のポジションに応募できる。同一候補者に複数のパイプラインレコードが存在しうる。

### 3. スカウト文の管理
スカウト文は「ポジション単位」で管理されており、バージョン管理（`Ver`）と媒体別（`DB`）の管理が可能。
スカウト送信後は `パイプライン` へのリレーションで追跡する。

### 4. ToDo DBが全DBと接続
ToDo DBはほぼ全ての業務DBとリレーションしており、タスクをどのオブジェクトに紐づけるかを
柔軟に管理できる。GTDのInbox→NextAction→Waitingのフローに対応。

### 5. 面談メモの統合管理
「打ち合わせ（クライアントとの）」と「候補者面談」と「面接同席」を同一DBで管理。
カテゴリで区別し、企業・ポジション・候補者・パイプラインいずれにも紐付け可能。

### 6. セキュリティ職種の専用フィールド
ポジションDBにはセキュリティ職種専用の `Sec-カテゴリ`、`Sec-守備範囲`、`Sec-役割レベル`、
`Sec-採用背景` フィールドがある。P&Lがセキュリティ人材の紹介に注力していることが伺える。

---

*最終更新: 2026-03-26*
