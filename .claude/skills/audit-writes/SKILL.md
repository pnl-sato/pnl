---
name: audit-writes
description: このセッション自身の Craft/Notion 書き込み失敗を自己点検し台帳行を出力（prompts/self-audit-write-failures.md）
---

`prompts/self-audit-write-failures.md` を読み込み、そこに書かれた手順を**このセッションに対して**そのまま実行してください。

要点（詳細は同ファイルが正本）：

- 見る範囲は**このセッションの会話履歴だけ**。他セッション・git・他ファイルを調べに行かない。
- 対象は、あなたがこのセッション中に行った Craft（`craft_write` 等）／Notion（`notion-create-pages`・`notion-update-page` 等）の書き込みで起きた**静かな失敗（成功扱いで返ったが構造が崩れていたもの）**。
- 本体の再修正・ファイル編集・commit はしない。**台帳行をテキストで出力するだけ**。
- 思い出せない範囲は捏造せず正直に申告する。
- Craft の事例は `agents/craft-writing.md` §0、Notion の事例は `agents/notion-writing.md` §0 の列に合わせた行で出力する。

$ARGUMENTS
