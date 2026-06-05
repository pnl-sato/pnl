---
description: 候補者プロファイル md を作成・追記・同期する（agents/candidate-profile.md を読み込み）
---

`agents/candidate-profile.md` を読み込んで、候補者プロファイルモードに入ってください。

引数 `$ARGUMENTS` には候補者の `{姓}` または `{姓 名}` が含まれます。引数の後に操作キーワード（`同期` `追記` `生成` など）が続く場合はそれをモードヒントとして使ってください。

動作モード（`agents/candidate-profile.md` セクション2の判定ロジックに従う。判定は Craft 上のドキュメント有無で行う）：
- 候補者名が言及された → **読み込み**（Craft フォルダ `13_Candidate｜候補者` を `search` → 該当ドキュメントを取得して展開）
- Craft の `search` で該当ドキュメントが見つからない → **初回生成**（Notion / Salesforce / Gmail / Google Drive / LinkedIn / Slack から横断収集して Craft に新規作成）
- ユーザーが素材（メール本文・Slack 抜粋等）を貼り付け → **対話追記**
- 「同期」「最新反映」等の指示 → **同期更新**

重要：
- 候補者プロファイルは **Craft が唯一の正本**（folder ID: `05BC363C-0FC2-4B15-AB3D-7C335AA5AB4E`）。git・GitHub・他クラウドへの転載は禁止。
- `candidates/` ディレクトリは旧設計の名残で `.gitignore` 除外済み。**使わない**。
- 個人情報を含むため、取扱いに注意。「他社展開不可」等の指示があれば Craft md 冒頭の「留意事項」に明記する。

不明な点（候補者の特定、取得対象範囲など）はユーザーに確認してから進めてください。

$ARGUMENTS
