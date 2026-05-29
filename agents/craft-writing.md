# Craft 書き込み（craft_write）の落とし穴と対処

Craft MCP の `craft_write` で複数アイテム・複数ブロックを書こうとすると、改行・bullet 表示が壊れやすい。
このファイルは、実セッションで踏んだ失敗を整理し、次回以降の書き込みで同じ罠にハマらないようにするための運用メモ。

抽出ソース：2026-05-28 セッションでギフティ「法務リーダー」md（`positions/法務リーダー`）の書き込み試行錯誤、および同日のscout-kit Craft複製で確立した確実構文。

---

## 0. TL;DR（2026-05-28 セッションで実証された確実な投入手順）

**結論：実改行2つ（空行）で区切ったmarkdownを1コールで投入すれば、見出し・bullet・テーブル・コードブロックが正しく複数ブロックに分割される。**

| 投入したいもの | 確実な書き方 | 1コールで可？ |
| --- | --- | --- |
| 見出し（`## ...`） | そのまま | ◯ |
| bullet 群（`- a` × n個） | **各bulletの間に空行を入れる**（`- a\n\n- b\n\n- c`） | ◯ |
| 番号付きリスト（`1. ...`） | bullet と同様、空行区切り | ◯ |
| テーブル（`\| ... \|` 形式） | そのまま1ブロック | ◯ |
| コードブロック（```...```） | そのまま1ブロック | ◯ |
| インラインコード（`` `text` ``） | そのまま | ◯ |
| bold（`**text**`）・link（`[text](url)`） | そのまま | ◯ |
| **callout（`<callout>...</callout>`）** | **単独ブロックで送る**（本文と混在不可） | ◯（単独のみ） |
| 1ファイル全文 | 上記を組み合わせて1〜2コールで投入可能 | ◯ |

**重要：JSON 経由の MCP 呼び出しでは、文字列内の実改行はそのまま改行として craft に渡る。** したがって、`--markdown` パラメータに複数行 markdown を含む値を渡せば、craft の markdown パーサーが正しく解釈する。`\n\n` をリテラルで送る必要はない。

例（要件.md の `## 2. Must 要件` セクションを1コールで投入）：

```
blocks add --id <rootBlockId> --markdown "## 2. Must 要件

- 企業における法務実務経験 **3年以上**

- 以下いずれかの**主担当経験**

- 自走判断＋必要時に相談できるスタンス" --position end
```

→ craft は h2 ブロック1個＋ listStyle=bullet ブロック3個に正しく分解する。

### 重要な制約

- **callout だけは本文と混在NG。**`<callout>...</callout>` を含む markdown に他のブロック（見出し・段落・bullet）を一緒に入れると `Unexpected HTML token at this position` でエラー。callout は単独で `blocks add` する。
- **shell の `$'...'` 構文は使えない。**MCP は bash 経由ではなく直接JSON値として渡るので、`$'...'` の `$` がリテラル文字として保存される。空行を入れたければ、`--markdown` の値にそのまま実改行を含める（このメモ全体の例の通り）。
- **同一ドキュメントへの複数 `blocks add` の並列実行は順序保証なし。**同一ドキュメント内は逐次、別ドキュメント間は並列OK（3ドキュメントの本文を並列投入で時短可能）。

### 効率の目安

- 4ファイル × 平均8セクションの scout-kit を Craft に投入する場合、合計 **13コール程度**（各ドキュメント 3〜4コール）で完了。個別 bullet add で進めると 300+ コール必要だったのが、空行区切り一括投入で大幅短縮。

---

## 1. 何が起きるか（失敗パターン）

### 症状A：箇条書きが `\n` リテラルで保存される

`blocks add --markdown "- item1\n- item2\n- item3"` のように `\n` を区切り文字として使うと、
**Craft 側で1つのブロックに `- item1\n- item2\n- item3` の文字列として保存される**。
結果、bullet リストとして描画されず、`\n` が文字として可視化される。

### 症状B：実改行で分割すると bullet スタイルが外れる

`--markdown` に実改行を埋め込むと（JSON経由で送る場合 `"\n"` は実改行になる）、Craft は最初の `\n\n` 相当をブロック区切りとして扱うが、
**2ブロック目以降の bullet スタイル（`- `）は引き継がれず、プレーン段落になる**ことがある。

---

## 2. なぜ起きるか

`craft_write` ツール説明の原文：

> `--markdown "p1\n\np2"` => multiple blocks; single `\n`=literal; real NL=soft breaks

これを正しく読むと：

- **`\n\n`（リテラルでバックスラッシュ＋n を2回）** = 複数ブロック区切り
- **単一 `\n`（リテラル）** = literal `\n`（文字として保存される）
- **実改行（バイト 0x0A）** = soft break（同一ブロック内の改行）

ところが JSON 文字列としてツールに渡るとき：

- JSON の `"\n"` は実改行（0x0A）にデコードされる
- JSON の `"\\n"` がリテラル `\n`（バックスラッシュ＋n）になる

つまり、**JSON 経由でツールを呼ぶ時、リテラル `\n\n` を送るには `"\\n\\n"` と書く必要がある**。
普通に `"\n\n"` と書くと実改行2つになり、Craft 側で「soft break × 2」or「ブロック区切り」のどちらに解釈されるかが不安定。
さらに `- ` のスタイル継承も期待通りに動かない。

---

## 3. 推奨ベストプラクティス

### 3-1. 大きなドキュメントは「セクション単位」で逐次 add

1ショットで全部書こうとせず、**見出し1つ＋bullet 1つを別々に `blocks add` する**。
ツール呼び出し回数は増えるが、確実に意図通りの構造になる。

```
blocks add --id <rootBlockId> --markdown "## 留意事項" --position end
blocks add --id <rootBlockId> --markdown "- 5/27 オープンの新規ポジション" --position end
blocks add --id <rootBlockId> --markdown "- 報酬レンジ 700〜1,000万＋SO" --position end
blocks add --id <rootBlockId> --markdown "## 1. ポジション要件" --position end
...
```

### 3-2. 1ブロック内に複数の bullet を入れたい場合

- **シェル経由なら `--markdown $'item1\nitem2'` のように bash の `$'...'` で実改行を明示的に入れる**（soft break＝1ブロック内の改行）
- ただしこれは「1ブロックの中に2行のテキスト」になるだけで、bullet として2itemにはならない
- 確実に bullet 2item にしたいなら 3-1 の方式（個別 add）が安全

### 3-3. テーブル・コードブロックは1ブロックで送って良い

テーブル記法は元々1ブロックなので、`\n` 区切りで送って問題なし。
ただし JSON 経由なら実改行が soft break として解釈されるので、テーブル罫線の `|` が正しく改行されるか `blocks get` で確認すること。

### 3-4. 書き込み後は必ず確認

```
blocks get <rootBlockId> --depth 5 --format markdown
```

- 出力に `\n` リテラルが見えたら失敗
- bullet が `- ` プレフィックス付きで各行に来ているか
- 想定したブロック数になっているか

---

## 4. アンチパターン集

| やったこと | 結果 | 対処 |
|---|---|---|
| `blocks add --markdown "- a\n- b\n- c"`（リテラル `\n` で区切り） | 1ブロックに `- a\n- b\n- c` がリテラル保存 | **空行（実改行2つ）区切り**で送る（セクション0参照） |
| `blocks update --markdown "item1\n\nitem2"`（実改行2つ・bullet なし） | 最初のブロックだけ反映、残りはプレーン段落 | 各itemに `- ` を付けて空行区切りで投入 |
| `blocks add --markdown $'- a\n- b'`（bash の `$'...'` 構文） | `$` がリテラル化、`\n` も literal `\n` として保存 | **`$'...'` は使えない**。`--markdown "..."` の値に実改行を直接含める |
| `blocks add --markdown "<callout>x</callout>\n\n## 見出し\n\n本文"`（calloutと他要素混在） | `Unexpected HTML token at this position` エラー | **callout は単独で `blocks add`**、本文は別コール |
| 長文markdown を1回の `blocks add` で送信（リテラル `\n` 区切り） | 半分以上のブロックで改行リテラル化 | 空行区切りで送れば1コールでも可（セクション0参照） |
| 書き込み後の確認をスキップ | 後で読みづらいと判明、再修正に時間 | 必ず `blocks get` で表示確認 |

---

## 5. 失敗からのリカバリ手順

すでに `\n` リテラルが保存されたブロックを修正する場合：

1. `blocks get <rootBlockId> --depth 5 --format markdown` で問題ブロックの ID を特定
2. 各itemを個別 `blocks update --id <blockId> --markdown "- item1"` で1つだけに上書き
3. 残りのitemを `blocks add --siblingId <blockId> --markdown "- item2" --position after` で順次挿入
4. または、ドキュメント自体を `documents create` で新しく作り直す方が早いケースも多い（既存リンクが少ない場合）

---

## 6. このファイルに更新があった時

新しい失敗パターンや確実に動く構文を見つけたら、本ファイルに追記する。
CLAUDE.md セクション10 の運用ルールに従い、**main にも反映**すること（他セッションが参照できるよう）。
