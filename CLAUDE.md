# pnl repository rules

## このリポジトリの目的
このリポジトリは「採用エージェント業務の自動化」を目的とする。

対象業務：
- 候補者管理（Notion）
- スカウト管理
- 面談管理
- パイプライン管理
- 転職検知
- Slack通知
- 各種データ同期（LinkedIn / YOUTRUST / Bizreach など）

最終的には「営業活動をほぼ自動化する基盤」を目指す。

---

## 設計思想

### 1. シンプル優先
- まず動くことを最優先
- 過剰な設計は禁止
- 小さく作ってあとで拡張する

### 2. スクリプト単位で完結
- 1機能 = 1スクリプト
- 依存関係を最小化
- 単体で動く構造にする

### 3. 非エンジニア運用前提
- コードは読みやすくする
- 日本語コメント必須
- 設定値は上部にまとめる
- コピペで動く構成にする

### 4. 再利用性
- Notion連携は共通関数化
- Slack通知も共通化
- API処理はできるだけ統一

---

## ディレクトリ構成ルール

```
pnl/
├── scripts/   # 実行スクリプト
├── modules/   # 共通処理（Notion / Slack / APIなど）
├── config/    # 設定ファイル
└── docs/      # ドキュメント
```

※まだ無くてもOK、今後この形に寄せていく

---

## コードルール

- Pythonで統一（当面）
- 日本語コメントを書く
- printログを必ず入れる
- エラー時のログを出す
- APIキーなどは直書きしない（環境変数 or config）

---

## 現在のファイル構成

```
pnl/
├── CLAUDE.md
├── notion_structure.md              # Notion DB構造の詳細（要参照）
└── scripts/
    ├── linkedin_notion_sync.py      # LinkedIn → Notion 送信数同期（実装済み）
    └── requirements.txt
```

---

## セットアップ

```bash
pip install -r scripts/requirements.txt
playwright install chromium

# .env ファイルを作成して以下を設定
NOTION_TOKEN=xxx
SCOUT_DB_ID=xxx   # 省略可（デフォルト値あり）
```

## 実行コマンド

```bash
python scripts/linkedin_notion_sync.py               # 通常実行
python scripts/linkedin_notion_sync.py --save-session  # 初回ログイン（ブラウザが開く）
python scripts/linkedin_notion_sync.py --dry-run     # Notionを更新せず確認のみ
python scripts/linkedin_notion_sync.py --debug       # 詳細ログ＋HTMLデバッグ保存
python scripts/linkedin_notion_sync.py --force       # 同日2回目も強制実行
```

## LinkedIn プロジェクト名の規則

`{ポジション名}-{企業コード}` 形式であること。

例：
- `事業企画担当-GFT`
- `セキュリティ統括責任者（CISO候補）-GFT`
- `社長候補-BST`

---

## 実装優先順位

1. LinkedIn → Notion 同期（実装済み）
2. Slack通知
3. 転職検知（SNS巡回）
4. 面談ログ自動登録
5. スカウト送信管理

---

## 禁止事項

- 不要に複雑な設計
- フレームワークの導入（当面）
- ブラックボックス化
- 動かないサンプルコード

---

## 重要

常に「非エンジニアが運用できるか」を最優先にすること。
