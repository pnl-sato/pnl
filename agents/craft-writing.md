# Craft 書き込み（craft_write）の落とし穴と対処

Craft MCP の `craft_write` で複数アイテム・複数ブロックを書こうとすると、改行・bullet 表示が壊れやすい。
このファイルは、**実セッションで踏んだ Craft 書き込みの失敗→成功事例を集合知として蓄積する正本**。次回以降の書き込みで同じ罠にハマらないようにするための運用メモ。

- **Craft 書き込みの失敗はこのファイル**、**Notion 書き込みの失敗は `agents/notion-writing.md`** に、それぞれ独立したケース台帳として貯める（佐藤指示：書き込み先は Craft と Notion がほぼ全てなので、媒体ごとに集合知管理する）。
- どちらにも共通する「host connector の書込が `requires approval` で wedge する事象」は **`CLAUDE.md` §14** が正本。両ファイルからは相互参照にとどめる。

**台帳の境界線ルール（どこに何を書くか／いつ新ファイルを作るか）：**

- **媒体固有の format・behavior の失敗**（`\n`リテラル化・トグル二段・`documents rename`不在 など Craft 特有のもの）→ **その媒体の台帳**（Craft はこのファイル）に書く。
- **媒体横断の connector wedge**（`requires approval`）→ **`CLAUDE.md` §14 のみ**に集約し、台帳からは相互参照（C6 のように索引行だけ置く）。両台帳に実体を二重記載しない。
- **新しい書き込み先**（Gmail 下書き・Calendar・Slack・SF・Drive 等）→ **先回りで台帳を作らない**。実際に書き込み失敗が再発したら初めて専用台帳を起こす。それまで connector 系の失敗は §14 を catch-all にする。

---

## 0. セッション別ケース台帳（集合知）

新しい失敗を踏んで解決したら、まずこの表に**1行追記**してから（必要なら）下の詳細セクションを更新する。詳細は各ケースの「恒久ルール参照」先に書く。

| # | 日付・セッション | 失敗症状 | トリガー（やったこと） | 成功手順（直し方） | 恒久ルール参照 |
|---|---|---|---|---|---|
| C1 | 2026-05-28 ギフティ「法務リーダー」md（`positions/法務リーダー`） | bullet が `\n` リテラルで1ブロックに保存／2ブロック目以降の bullet スタイルが外れる | `blocks add --markdown "- a\n- b\n- c"`（実改行1つで区切り） | 各 bullet を**空行1行（実改行2つ）**で区切り、各行に `- ` を付ける。確実性重視なら見出し＋bullet を個別 add | §1, §3-1, §3-2b |
| C2 | 2026-05-28 scout-kit 複製 | `Unexpected HTML token at this position` で書き込み全体がエラー | `<callout>` を本文・見出しと同一 `blocks add` で混在させた | **callout は単独で `blocks add`**、本文は別コールに分ける | §3-2b 注, §4 |
| C3 | 2026-05〜06 | `$` がリテラル化、`\n` も literal `\n` として保存 | `blocks add --markdown $'- a\n- b'`（bash の `$'...'` を使用） | MCP は bash 非経由。`--markdown "..."` の値に**直接実改行**を含める | §3-2, §4 |
| C4 | 2026-06 | トグルの子が中に入らず、兄弟ブロック／リテラル文字列になる | `+ 親\n\n\t- 子` で一発ネストを狙った | トグル枠を作ってから `blocks add --id <トグルID>` で子を入れる**二段方式** | §3-5, §4 |
| C5 | 2026-06 | タイトルを変えようとしてコマンドが見つからず詰まる | `documents rename` を探した（存在しない） | rootBlockId を `blocks update --id <rootBlockId> --markdown "新タイトル"`（**1行**）で更新＝本文保持のままリネーム | §3-6, §4 |
| C6 | （`requires approval` の wedge。実例は Notion 側） | `craft_write` が `requires approval` で止まる可能性（Craft も host connector なので同クラス） | コネクタ書込同意／MCP セッションの失効 | リトライ最大1回→読取で切り分け→成果物退避→新セッション。**Craft が落ちていればチャット最終ブロックに全文退避** | `CLAUDE.md` §14 / `agents/notion-writing.md` と共通 |

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

### 3-2b. ★効率化パターン：実改行 `\n\n` をブロック区切りとして使う（2026-05-28 検証）

セクション単位（見出し＋複数 bullet）を**1回の `blocks add` で確実に分割投下できる**ことが scout-kit 生成時に判明。アンチパターン集の symptom A／B を回避しつつ呼び出し回数を 1/5〜1/10 に削減できる。

**書き方：**

```
blocks add --id <pageId> --markdown "## セクション見出し

- bullet 1（**強調**OK）

- bullet 2

- bullet 3" --position end
```

- 各ブロックの間に**実改行を2つ**（空行1行）入れる
- bullet には行頭 `- ` を付ける（個別 add 時と同じ書式）
- 結果：見出し1ブロック＋bullet 3ブロックの計4ブロックが `listStyle: bullet` 付きで生成される

**動作確認済みのケース：**

| 構成 | 結果 |
|---|---|
| 見出し（##／###）＋bullet 群 | ✓ 見出しと各 bullet が個別ブロック化、bullet スタイル維持 |
| bullet のみ（`- a\n\n- b\n\n- c`） | ✓ 3つの独立 bullet ブロック |
| numbered list（`1. a\n\n2. b`） | ✓ `listStyle: numbered` で個別ブロック |
| bullet 内の `**強調**` | ✓ 太字 attribute 付与される |
| 段落（地の文）の連続 | ✓ 各段落が独立ブロック |

**craft_write の挙動メモ：**

JSON 経由の場合 `"\n"` は実改行（0x0A）にデコードされ、Craft CLI は「実改行2つ = ブロック区切り」として処理する（tool 説明の「real NL=soft breaks」だけ読むと soft break になりそうだが、`\n\n`（空行）はブロック区切りに昇格する）。`- ` プレフィックスもブロックごとに評価され、bullet スタイルが正しく付与される。

**それでも個別 add が安全な場面：**

- 同じセクション内で**段落 → bullet → 段落 → bullet** と頻繁に切り替わる場合（順序を厳密に保ちたい）
- `<callout>` を含める場合（**callout は単独 add 必須**。本文と混在すると `Unexpected HTML token` エラー）
- `+ Toggle title` など特殊記法を混在させる場合
- 1セクションのブロック数が 15 個を超える場合（一度にエラーが起きた時の復旧コストが上がる）

**並列実行の注意：**

- **同一ドキュメント**への複数 `blocks add` は順序保証なし → 逐次実行する
- **別ドキュメント**間は並列実行OK（複数ドキュメントへの本文投入を並列化して時短可能。scout-kit 複製で 3 ドキュメント並列投入を実用化）

**運用ルール：**

- 標準は「**1セクション（見出し＋関連 bullet 群）＝1回の `blocks add`**」
- 数行で済む短い構造は引き続き個別 add でも良い
- 書き込み後の `blocks get` 確認は必須（3-4 の手順は変わらず）

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

### 3-5. トグル（折りたたみ）でセクションを“子ページ化”してトークンを節約する（2026-06 検証）

長いプロファイル（候補者・クライアント md 等）で、毎回は読まない**重い尾部セクション**（面談メモ要約・Gmail/Slack 履歴・過去ログ）をトグルにすると、**概観の読みトークンを大きく削減**できる。実測で挙動を確定したので以下に従う。

**なぜ効くか（実測した挙動）：**

- `+ トグルタイトル` で**トグルブロック**（`listStyle: toggle`）になり、子を持たせると**コンテナ（`type: page`）に昇格**する。
- 親を `blocks get <rootBlockId> --depth 1` で読むと、**トグルはタイトル＋`contentPreview`（数行）だけ返り、本文（子ブロック）はロードされない**（`...and N more blocks` と省略表示）。つまり**重い本文を概観から除外**できる。
- 個々のトグルは `blocks get <トグルID> --depth N` で**そのセクションだけ**取得できる（ランダムアクセス）。トグルID は親の depth-1 出力にそのまま入っているので、**深掘りは追加 resolve 不要の1コール**で済む（別ドキュメント分割だと resolve-link→get の2コールになりがちで、ここがトグルの優位）。
- **二段トグル（トグルの中にトグル）**も可能。例：`面談メモ` トグルの中に面談1本ずつをトグル化すると、「最新の1本だけ」の粒度で取れる。

**作り方（★ネストは markdown 一発ではできない）：**

1. まずトグル枠を作る：`blocks add --id <pageId> --markdown "+ 4. 面談メモ・印象" --position end`（複数枠を `\n\n` 区切りで一括作成可、戻り値に各トグルIDが入る）。
2. 子は**トグルIDを親に指定して別コールで投入**：`blocks add --id <トグルID> --markdown "本文…" --position end`。1コール内で `\n\n` 区切りにすれば複数子ブロックをまとめて入れられる。
3. **重要：** markdown のタブ字下げ（`\t- 子`）や `+ 親\n\n- 子` のような“見た目のネスト”は**子にならず、リテラル文字列／兄弟ブロックになる**（実測）。必ず手順1→2の二段で入れる。

**運用方針（層別ハイブリッド。candidate-profile.md §4・client-profile.md §4 と対）：**

- **コア層（毎回使う）** = 留意事項・基本・選考状況など → **平の見出しのまま**（畳まない。即読みノーコスト）。
- **要約層（時々使う）** = 面談メモ要約・Gmail/Slack 要約 → **トグル**にし、各行に Notion/Drive の正本リンクを併記。概観は軽く、深掘りは名指し1コール。
- **生データ層（稀に必要）** = 面談メモ逐語・過去のやり取りログ・DM 全文 → **Craft に複製せず Notion/Drive のポインタだけ**置く。逐語が要るときだけ `notion-fetch` 等で取得（書きゼロ・常に最新）。

### 3-6. ドキュメントのタイトルをリネームする（★`documents rename` は無い／ルートブロックを `blocks update`）

Craft MCP に **`documents rename` は存在しない**（`documents` サブコマンドは create / move / delete のみ）。タイトルを変えたいときは、**ドキュメントのルートブロック（rootBlockId）を `blocks update` で1行テキストに更新する**と、**タイトルだけが変わり本文（子ブロック）は全て保持される**（2026-06 実測）。

```
blocks update --id <rootBlockId> --markdown "新しいタイトル"
```

- `<rootBlockId>` は `documents list --folder <id>` が返す先頭の ID（URL の documentId とは別物）。
- 戻り値の `after` で `type:"page"` の `title` が新タイトルになり、`markdown[]`（子ブロック）が元のまま並んでいることを確認する。
- markdown は**必ず1行**で渡す（複数ブロックにしない）。`blocks update` は「block[0] がターゲットを置換、残りは後ろに挿入」なので、複数行だと本文先頭に余計なブロックが入る。
- 別ドキュメント間は**並列実行 OK**。用途例：scout-kit 各ドキュメント名に `［ポジション名］` プレフィックスを付けて一覧での判別性を上げる（`agents/scout-kit.md` §6.1 の Claude.ai 命名規約と揃い、md エクスポート時のファイル名にもそのまま乗る）。

---

## 4. アンチパターン集

| やったこと | 結果 | 対処 |
|---|---|---|
| `blocks add --markdown "- a\n- b\n- c"`（1回で全bullet送信、実改行1つ区切り） | 1ブロックに `- a` のみ、残りは soft break として失われる or リテラル化 | bullet ごとに**空行1行**（実改行2つ）で区切る → 3-2b 参照 |
| `blocks update --markdown "item1\n\nitem2"`（実改行2つだが `- ` 無し） | 最初のブロックだけ bullet、残りはプレーン段落 | 各itemに `- ` を付ける（3-2b） |
| 長文markdown を区切り無しで1回送信 | 全部 soft break で1ブロックに圧縮 | `\n\n`（空行）でブロック区切り、3-2b の構成で送る |
| `<callout>x</callout>\n\n## 見出し\n\n本文`（callout を本文と混在） | `Unexpected HTML token at this position` で全体エラー | **callout は単独で `blocks add`**、本文は別コール（2026-05-28 scout-kit 複製で確認） |
| `blocks add --markdown $'- a\n- b'`（bash の `$'...'` 構文を使用） | `$` がリテラル化、`\n` も literal `\n` として保存 | **`$'...'` は使えない**（MCPは bash 経由でない）。`--markdown "..."` の値に直接実改行を含める（3-2b 参照） |
| 書き込み後の確認をスキップ | 後で読みづらいと判明、再修正に時間 | 必ず `blocks get` で表示確認 |
| `+ 親\n\n\t- 子` や `+ 親\n\n- 子` で一発ネストを狙う | 子がトグルの中に入らず、リテラル文字列／兄弟ブロックになる（2026-06 実測） | トグル枠を作ってから `blocks add --id <トグルID>` で子を入れる二段方式（3-5） |
| ドキュメントのタイトルを変えようと `documents rename` を探す | そんなコマンドは無い（create/move/delete のみ） | ルートブロックを `blocks update --id <rootBlockId> --markdown "新タイトル"`（1行）で更新＝本文保持のままリネーム（3-6） |

---

## 5. 失敗からのリカバリ手順

すでに `\n` リテラルが保存されたブロックを修正する場合：

1. `blocks get <rootBlockId> --depth 5 --format markdown` で問題ブロックの ID を特定
2. 各itemを個別 `blocks update --id <blockId> --markdown "- item1"` で1つだけに上書き
3. 残りのitemを `blocks add --siblingId <blockId> --markdown "- item2" --position after` で順次挿入
4. または、ドキュメント自体を `documents create` で新しく作り直す方が早いケースも多い（既存リンクが少ない場合）

---

## 6. このファイルに更新があった時（集合知の貯め方）

Craft 書き込みで**新しい失敗を踏んで解決した**ら、ユーザーの指示がなくても：

1. **§0 のケース台帳に1行追記**する（日付・セッション／失敗症状／トリガー／成功手順／恒久ルール参照）。これが最優先・最小コストの蓄積口。
2. 再発防止に書式やコマンドの一般則が要るなら、§3（ベストプラクティス）・§4（アンチパターン集）の該当箇所も更新する。
3. CLAUDE.md §12 の運用ルールに従い、**main にも fast-forward 反映**する（他セッションが参照できるよう）。

Notion 書き込みの失敗は本ファイルではなく **`agents/notion-writing.md`** の台帳に追記する。`requires approval` の wedge は両者共通なので **`CLAUDE.md` §14** に集約し、ここからは相互参照にとどめる。
