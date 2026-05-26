---
description: 候補者プロファイル md を作成・追記・同期する（agents/candidate-profile.md を読み込み）
---

`agents/candidate-profile.md` を読み込んで、候補者プロファイルモードに入ってください。

引数 `$ARGUMENTS` には候補者の `{姓}` または `{姓 名}` が含まれます。引数の後に操作キーワード（`同期` `追記` `生成` など）が続く場合はそれをモードヒントとして使ってください。

動作モード（agents/candidate-profile.md セクション1の判定ロジックに従う）：
- `candidates/{slug}.md` が存在しない → **初回生成**（Notion / Salesforce / Gmail / Slack から横断収集）
- ユーザーが素材（メール本文・Slack 抜粋等）を貼り付け → **対話追記**
- 「同期」「最新反映」等の指示 → **同期更新**

重要：
- `candidates/` は `.gitignore` で git 管理対象外。コミットしない。
- 個人情報を含むため、取扱いに注意。

不明な点（候補者の特定、取得対象範囲など）はユーザーに確認してから進めてください。

$ARGUMENTS
