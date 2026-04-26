# 引き継ぎ：Notion「関係性ステータス」プロパティ追加

## やること
既存の候補者管理 or 接点管理DBに、Notion APIで Selectプロパティを1つ追加する。

## 追加するプロパティの仕様

| 項目 | 値 |
|------|-----|
| プロパティ名 | `関係性ステータス` |
| 型 | `select` |

### 選択肢

| 名前 | カラー |
|------|--------|
| `🔥 今すぐ動かす` | `red` |
| `📅 中期ストック` | `yellow` |
| `💤 低優先` | `gray` |

## 実装手順

### 1. 必要な情報をユーザーに確認

- **Notion APIトークン**：`secret_xxx...` の形式
- **データベースID**：対象DBのURL `https://notion.so/xxx/[DATABASE_ID]?v=...` の部分

### 2. Notion APIでプロパティを追加

以下のPythonスクリプトを `scripts/add_notion_property.py` に作成して実行する。

```python
import requests

NOTION_TOKEN = "secret_xxx"   # ユーザーから取得
DATABASE_ID = "xxx"            # ユーザーから取得

url = f"https://api.notion.com/v1/databases/{DATABASE_ID}"
headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

payload = {
    "properties": {
        "関係性ステータス": {
            "select": {
                "options": [
                    {"name": "🔥 今すぐ動かす", "color": "red"},
                    {"name": "📅 中期ストック", "color": "yellow"},
                    {"name": "💤 低優先",       "color": "gray"}
                ]
            }
        }
    }
}

response = requests.patch(url, headers=headers, json=payload)
print(response.status_code, response.json())
```

### 3. 動作確認

- ステータスコード `200` が返れば成功
- Notionを開いてDBに `関係性ステータス` が追加されているか目視確認

## 今後の拡張余地（対応不要、参考情報）

- `次回接触予定日`（Date型）を同様の手順で追加 → Zapier/Makeでリマインド自動化に使える
- 選択肢ごとのアクション定義：
  - `🔥 今すぐ動かす`：24h以内にフォロー連絡
  - `📅 中期ストック`：3ヶ月以内に再接触
  - `💤 低優先`：半年放置OK
