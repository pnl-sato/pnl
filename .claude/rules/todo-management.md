---
description: ToDo 管理の必須制約
---
- ToDo は Notion ToDo DB（`collection://2257d017-b6a0-8026-867c-000bb0969507`）に一元化。Craft md に独自の ToDo セクションを作らない
- **残タスク一覧は必ず `python3 tools/notion_active_todos.py --work` で取得する**。`notion-search` で ToDo を列挙してはならない（ステータスが見えず完了済みが混ざる事故が発生済み）
- `--category "Pole&Line"`（include 方式）は使わない。`--work`（exclude 方式）を使う（Category 空欄の業務タスク取りこぼし防止）
- 新規 ToDo は `Inbox 📨` で作成。Title プレフィックスは `[Claude]` or `[佐藤]`
