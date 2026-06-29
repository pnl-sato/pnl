#!/usr/bin/env python3
"""Notion「送信ログ」DB への create / back-fill（NOTION_TOKEN 直叩き＝MCP 非依存）。

役割（agents/scout-kit.md §4.5／§6.5）:
    未接触候補のスカウト適性評価を 1行=1評価 で蓄積する正本台帳への書き込み口。
    通常は Claude.ai が MCP（notion-create-pages）で評価時に create するが、Notion 書込が
    「承認待ち」で wedge する事象（CLAUDE.md §14）があるため、その**フォールバック**と、
    返信時のアウトカム**書き戻し**を MCP に依存せず実行できるようにする。

    media_id（＝DB の title）が SF Contact への join キー。
      BizReach=会員ID / LinkedIn=プロフィールURL の vanity slug / その他=媒体の安定ID。

使い方:
    # ① 評価時の create（フォールバック or スクリプト投入）
    NOTION_TOKEN=ntn_xxx python3 tools/scout_eval.py create \
        --media-id BU5854836 --media Bizreach --eval-date today \
        --gen-cat "SaaS" --score 82 --judge A --persona P1 --age-band 30代前半 \
        --inferred --memo "推定スコープ広め、面談で裏取り" [--signal "枠判定=リーダー枠"] \
        [--position <page_id|url>] [--scout <page_id|url>]

    # Claude.ai のフォールバック「登録ブロック」をそのまま取り込む（パイプ1行・11列）
    python3 tools/scout_eval.py create --from-block \
        "2026-06-12｜BU5854836｜Bizreach｜SaaS｜82｜A｜P1｜30代前半｜YES｜所感…｜枠判定=リーダー枠"

    # ③ 返信時のアウトカム書き戻し（媒体IDで該当行を引いて update）
    python3 tools/scout_eval.py backfill \
        --media-id BU5854836 --reply-date today --result 面談 --sf-id 003xxxxxxxxxxxx \
        [--sent-date 2026-06-10] [--candidate <page_id|url>]

    # 評価フレームが出力した「登録ブロック（パイプ1行）」をファイルにまとめて一括取り込み
    python3 tools/scout_eval.py bulk --from-block-file lines.txt --position <page_id|url> [--dry-run]

    # 確認
    python3 tools/scout_eval.py find --media-id BU5854836
    python3 tools/scout_eval.py list [--limit 20]

登録ブロックの列順（scout-kit §4.5）:
    評価日｜媒体ID｜媒体｜現職カテゴリ｜総合点｜判定｜ペルソナ｜年代バンド｜推定heavy｜一言所感｜追加シグナル
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta, date

TOKEN = os.environ.get("NOTION_TOKEN")
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
JST = timezone(timedelta(hours=9))

EVAL_DB = "42e00d7d5c7e4b09aa80d6c1cd4e55bf"   # 送信ログ（database id）

JUDGES = {"A", "B", "C", "D"}
PERSONAS = {"P1", "P2", "P3"}
AGE_BANDS = {"20代後半", "30代前半", "30代後半", "40代前半", "40代後半", "50代〜"}
RESULTS = {"面談", "推薦", "通過", "見送り", "辞退"}
TRUTHY = {"yes", "__yes__", "true", "1", "○", "推定heavy", "推定", "y"}

# 登録ブロック（パイプ1行）の列順
BLOCK_COLS = ["評価日", "媒体ID", "媒体", "現職カテゴリ", "総合点",
              "判定", "ペルソナ", "年代バンド", "推定heavy", "一言所感", "追加シグナル"]


def _req(path, payload=None, method="POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": VERSION,
                 "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        sys.exit(f"Notion API error {e.code}: {e.read().decode()[:400]}")


def resolve_date(s):
    if s in (None, ""):
        return None
    if s == "today":
        return datetime.now(JST).date().isoformat()
    if s == "yesterday":
        return (datetime.now(JST).date() - timedelta(days=1)).isoformat()
    return date.fromisoformat(s).isoformat()


def page_id(s):
    """page id or URL から 32桁hex を取り出して dash 付き UUID に整形。"""
    if not s:
        return None
    m = re.findall(r"[0-9a-fA-F]{32}", s.replace("-", ""))
    if not m:
        sys.exit(f"relation の page id を特定できない: {s}")
    h = m[-1]
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _opt(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args else default


def _title(v):
    return {"title": [{"text": {"content": v}}]} if v else {"title": []}


def _text(v):
    return {"rich_text": [{"text": {"content": v}}]} if v else {"rich_text": []}


def _select(v):
    return {"select": {"name": v}} if v else {"select": None}


def _date(v):
    return {"date": {"start": v}} if v else {"date": None}


def _rel(v):
    return {"relation": [{"id": page_id(v)}]} if v else {"relation": []}


def build_props_create(d):
    """d: 正規化済み dict → Notion properties。空値は送らない。"""
    p = {}
    if not d.get("media_id"):
        sys.exit("--media-id（媒体ID＝title）は必須")
    p["媒体ID"] = _title(d["media_id"])
    if d.get("eval_date"):    p["評価日"] = _date(d["eval_date"])
    if d.get("media"):        p["媒体"] = _select(d["media"])
    if d.get("gen_cat"):      p["現職カテゴリ"] = _text(d["gen_cat"])
    if d.get("score") is not None: p["総合点"] = {"number": d["score"]}
    if d.get("judge"):        p["判定"] = _select(d["judge"])
    if d.get("persona"):      p["ペルソナ"] = _select(d["persona"])
    if d.get("age_band"):     p["年代バンド"] = _select(d["age_band"])
    if d.get("inferred") is not None: p["推定heavy"] = {"checkbox": bool(d["inferred"])}
    if d.get("memo"):         p["一言所感"] = _text(d["memo"])
    if d.get("signal"):       p["追加シグナル"] = _text(d["signal"])
    if d.get("position"):     p["ポジション"] = _rel(d["position"])
    if d.get("scout"):        p["スカウト文"] = _rel(d["scout"])
    if d.get("sent_date"):    p["送信日"] = _date(d["sent_date"])
    if d.get("reply_date"):   p["返信日"] = _date(d["reply_date"])
    if d.get("result"):       p["結果"] = _select(d["result"])
    if d.get("sf_id"):        p["SF id"] = _text(d["sf_id"])
    return p


def find_rows(media_id, limit=25):
    res = _req(f"/databases/{EVAL_DB}/query",
               {"filter": {"property": "媒体ID", "title": {"equals": media_id}},
                "page_size": limit})
    return res["results"]


def _plain_title(pr):
    t = pr.get("媒体ID", {}).get("title", [])
    return "".join(x.get("plain_text", "") for x in t)


def _val(pr, name):
    v = pr.get(name, {})
    t = v.get("type")
    if t == "select":
        return (v.get("select") or {}).get("name")
    if t == "date":
        return (v.get("date") or {}).get("start")
    if t == "number":
        return v.get("number")
    if t == "checkbox":
        return v.get("checkbox")
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in v.get("rich_text", []))
    return None


def _block_to_dict(blk):
    """登録ブロック（パイプ1行・BLOCK_COLS 順）→ create 用 dict。"""
    parts = [c.strip() for c in re.split(r"[｜|]", blk)]
    if len(parts) < len(BLOCK_COLS):
        sys.exit(f"登録ブロックは {len(BLOCK_COLS)}列（{'｜'.join(BLOCK_COLS)}）。受領 {len(parts)}列: {blk[:60]}")
    m = dict(zip(BLOCK_COLS, parts))
    return {
        "eval_date": resolve_date(m["評価日"]) if m["評価日"] else None,
        "media_id": m["媒体ID"], "media": m["媒体"] or None,
        "gen_cat": m["現職カテゴリ"] or None,
        "score": float(m["総合点"]) if m["総合点"] else None,
        "judge": m["判定"] or None, "persona": m["ペルソナ"] or None,
        "age_band": m["年代バンド"] or None,
        "inferred": m["推定heavy"].strip().lower() in TRUTHY,
        "memo": m["一言所感"] or None, "signal": m["追加シグナル"] or None,
    }


def cmd_create(args):
    blk = _opt(args, "--from-block")
    if blk:
        d = _block_to_dict(blk)
    else:
        score = _opt(args, "--score")
        d = {
            "media_id": _opt(args, "--media-id"),
            "eval_date": resolve_date(_opt(args, "--eval-date", "today")),
            "media": _opt(args, "--media"),
            "gen_cat": _opt(args, "--gen-cat"),
            "score": float(score) if score is not None else None,
            "judge": _opt(args, "--judge"),
            "persona": _opt(args, "--persona"),
            "age_band": _opt(args, "--age-band"),
            "inferred": ("--inferred" in args),
            "memo": _opt(args, "--memo"),
            "signal": _opt(args, "--signal"),
            "position": _opt(args, "--position"),
            "scout": _opt(args, "--scout"),
        }
    # 値の軽い検証（typo を弾く）
    if d.get("judge") and d["judge"] not in JUDGES:
        sys.exit(f"--judge は {sorted(JUDGES)} のいずれか: {d['judge']}")
    if d.get("persona") and d["persona"] not in PERSONAS:
        sys.exit(f"--persona は {sorted(PERSONAS)} のいずれか: {d['persona']}")
    if d.get("age_band") and d["age_band"] not in AGE_BANDS:
        sys.exit(f"--age-band は {sorted(AGE_BANDS)} のいずれか: {d['age_band']}")

    dup = find_rows(d["media_id"])
    if dup:
        print(f"⚠ 媒体ID '{d['media_id']}' の行が既に {len(dup)}件あります（重複 create に注意）。"
              f"アウトカム追記なら `backfill` を使ってください。", file=sys.stderr)
    res = _req("/pages", {"parent": {"database_id": EVAL_DB},
                          "properties": build_props_create(d)})
    print(f"✓ create: 媒体ID={d['media_id']} 判定={d.get('judge') or '-'} "
          f"推定heavy={'YES' if d.get('inferred') else 'no'} → {res.get('url')}")


def cmd_backfill(args):
    media_id = _opt(args, "--media-id")
    if not media_id:
        sys.exit("--media-id は必須")
    rows = find_rows(media_id)
    if not rows:
        print(f"× 媒体ID '{media_id}' の評価行が見つからない（scout-kit 経由でない候補者＝スキップ）")
        return
    if len(rows) > 1:
        print(f"⚠ 媒体ID '{media_id}' が {len(rows)}件ヒット。評価日が新しい行を更新します。", file=sys.stderr)
        rows.sort(key=lambda p: _val(p["properties"], "評価日") or "", reverse=True)
    target = rows[0]

    props = {}
    sent = resolve_date(_opt(args, "--sent-date"))
    reply = resolve_date(_opt(args, "--reply-date"))
    result = _opt(args, "--result")
    sf_id = _opt(args, "--sf-id")
    cand = _opt(args, "--candidate")
    if sent:    props["送信日"] = _date(sent)
    if reply:   props["返信日"] = _date(reply)
    if result:
        if result not in RESULTS:
            sys.exit(f"--result は {sorted(RESULTS)} のいずれか: {result}")
        props["結果"] = _select(result)
    if sf_id:   props["SF id"] = _text(sf_id)
    if cand:    props["候補者"] = _rel(cand)
    if not props:
        sys.exit("更新する項目がない（--reply-date / --result / --sf-id / --sent-date / --candidate のいずれか）")

    _req(f"/pages/{target['id']}", {"properties": props}, method="PATCH")
    print(f"✓ backfill: 媒体ID={media_id} "
          f"返信日={reply or '-'} 結果={result or '-'} SFid={sf_id or '-'} → {target.get('url')}")


def cmd_find(args):
    media_id = _opt(args, "--media-id")
    if not media_id:
        sys.exit("--media-id は必須")
    rows = find_rows(media_id)
    if not rows:
        print("（該当なし）")
        return
    for p in rows:
        pr = p["properties"]
        print(f"  {_val(pr,'評価日') or '----'} | {_plain_title(pr):14} | "
              f"判定{_val(pr,'判定') or '-'} {_val(pr,'ペルソナ') or '--'} "
              f"推定{'Y' if _val(pr,'推定heavy') else '-'} | "
              f"送信{_val(pr,'送信日') or '-'} 返信{_val(pr,'返信日') or '-'} "
              f"結果{_val(pr,'結果') or '-'} | {p.get('url')}")


def cmd_list(args):
    limit = int(_opt(args, "--limit", "20"))
    res = _req(f"/databases/{EVAL_DB}/query",
               {"page_size": limit,
                "sorts": [{"property": "評価日", "direction": "descending"}]})
    for p in res["results"]:
        pr = p["properties"]
        print(f"  {_val(pr,'評価日') or '----'} | {_plain_title(pr):14} | "
              f"{_val(pr,'媒体') or '-':9} 判定{_val(pr,'判定') or '-'} "
              f"{_val(pr,'ペルソナ') or '--'} 推定{'Y' if _val(pr,'推定heavy') else '-'} | "
              f"返信{_val(pr,'返信日') or '-'} 結果{_val(pr,'結果') or '-'}")


# ---- bulk 取り込み（Craft 旧台帳 → Notion 評価ログDB 移行用） ----

COL_ALIASES = {
    "評価日": "eval_date", "日付": "eval_date",
    "媒体id": "media_id", "イニシャル": "media_id",
    "現職カテゴリ": "gen_cat", "枠判定": "waku", "総合点": "score",
    "判定": "judge", "一言所感": "memo",
    "年代": "nendai", "年代(p#)": "nendai",
    "送信日": "sent_date", "返信日": "reply_date", "結果": "result",
    "sfid": "sf_id", "sf id": "sf_id",
}
# 語句判定 → A/B/C/D（出現順に判定。"見送り" を含む "送付" 誤爆を避けるため "見送り" を先に）
WORD_JUDGE = [("積極送付", "A"), ("見送り", "D"), ("スカウト送付", "B"),
              ("送付", "B"), ("要再評価", "C"), ("再評価", "C")]


def _num(s):
    m = re.search(r"-?\d+(?:\.\d+)?", s or "")
    return float(m.group()) if m else None


def _strip_fallback(mid):
    return re.sub(r"（媒体[Ii][Dd]＝氏名フォールバック）", "", mid or "").strip()


def _map_judge(raw, mode):
    raw = (raw or "").strip()
    m = re.match(r"\s*([ABCDabcd])\b", raw) or re.match(r"\s*([ABCDabcd])", raw)
    if m and (mode != "words" or not re.search(r"[一-龯ぁ-んァ-ン]", raw[:2])):
        return m.group(1).upper()
    for w, l in WORD_JUDGE:
        if w in raw:
            return l
    if m:
        return m.group(1).upper()
    return None


def _media_auto(media_id, default):
    if re.match(r"^BU\d+$", media_id or ""):
        return "Bizreach"
    return default or "Linkedin"   # 本データの非BizReach（slug/氏名）は LinkedIn 由来


def _nendai(raw):
    """'40代前半/P2' -> ('P2','40代前半',None) / '40歳/P2' -> ('P2',None,'年代=40歳/P2')"""
    raw = (raw or "").strip()
    if not raw or raw == "-":
        return None, None, None
    parts = re.split(r"[/／]", raw)
    band_part = parts[0].strip() if parts else ""
    persona_part = parts[1].strip() if len(parts) > 1 else ""
    band = band_part if band_part in AGE_BANDS else None
    pm = re.match(r"^(P[123])$", persona_part)
    persona = pm.group(1) if pm else None
    stash = f"年代={raw}" if (band_part and not band) or (persona_part and not persona) else None
    return persona, band, stash


def _row_dict(cells, cols, a):
    raw = {}
    for i, col in enumerate(cols):
        key = COL_ALIASES.get(col.strip().lower())
        if key:
            raw[key] = cells[i].strip() if i < len(cells) else ""
    media_id = _strip_fallback(raw.get("media_id", ""))
    if not media_id:
        return None
    judge = _map_judge(raw.get("judge"), a["judge_map"])
    persona, band, nendai_stash = _nendai(raw.get("nendai"))
    sig = []
    if a.get("sig_prefix"):
        sig.append(a["sig_prefix"])
    if raw.get("waku"):
        sig.append(f"枠判定={raw['waku']}")
    if nendai_stash:
        sig.append(nendai_stash)
    memo = raw.get("memo") or ""
    rj = (raw.get("judge") or "").strip()
    if rj and rj != (judge or ""):
        memo = (memo + f" ｜判定注記:{rj}").strip()
    return {
        "media_id": media_id,
        "eval_date": resolve_date(raw["eval_date"]) if raw.get("eval_date") else None,
        "media": _media_auto(media_id, None if a["media"] == "auto" else a["media"]),
        "gen_cat": raw.get("gen_cat") or None,
        "score": _num(raw.get("score")),
        "judge": judge,
        "persona": persona,
        "age_band": band,
        "inferred": False,
        "memo": memo or None,
        "signal": " / ".join(sig) if sig else None,
        "sent_date": resolve_date(raw["sent_date"]) if raw.get("sent_date") else None,
        "reply_date": resolve_date(raw["reply_date"]) if raw.get("reply_date") else None,
        "result": raw.get("result") or None,
        "sf_id": raw.get("sf_id") or None,
        "position": a.get("position"),
        "scout": a.get("scout"),
    }


def _exists(media_id, position):
    pid = _norm(page_id(position)) if position else None
    for p in find_rows(media_id, 50):
        rel = {_norm(r["id"]) for r in p["properties"].get("ポジション", {}).get("relation", [])}
        if (pid is None and not rel) or (pid and pid in rel):
            return True
    return False


def _norm(uuid):
    return uuid.replace("-", "").lower()


def cmd_bulk(args):
    # 評価フレームが出力した「登録ブロック（パイプ1行）」のファイルをそのまま一括 create
    fbf = _opt(args, "--from-block-file")
    if fbf:
        position = _opt(args, "--position")
        scout = _opt(args, "--scout")
        rows = []
        with open(fbf, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("評価日") or len(re.findall(r"[｜|]", line)) < 8:
                    continue   # 空行・フォーマット見本行・非ブロック行はスキップ
                d = _block_to_dict(line)
                d["position"] = position
                d["scout"] = scout
                rows.append(d)
        print(f"parsed {len(rows)} 行 ← {fbf}（from-block）")
        if "--dry-run" in args:
            for d in rows:
                print("  ", {k: d[k] for k in ("eval_date", "media_id", "media",
                                               "score", "judge", "persona", "age_band")})
            print(f"[dry-run] {len(rows)} 行・書き込みなし")
            return
        created = skipped = 0
        for d in rows:
            if _exists(d["media_id"], d["position"]):
                skipped += 1
                continue
            _req("/pages", {"parent": {"database_id": EVAL_DB}, "properties": build_props_create(d)})
            created += 1
        print(f"✓ bulk(from-block): created {created} / skipped(dup) {skipped} → position {position or '(なし)'}")
        return

    path = _opt(args, "--file")
    cols_s = _opt(args, "--cols")
    if not path or not cols_s:
        sys.exit("bulk: --file と --cols（ソース列名のカンマ区切り）は必須")
    a = {
        "position": _opt(args, "--position"),
        "scout": _opt(args, "--scout"),
        "media": _opt(args, "--media", "auto"),
        "judge_map": _opt(args, "--judge-map", "letters"),
        "sig_prefix": _opt(args, "--sig-prefix"),
    }
    cols = [c.strip() for c in cols_s.split(",")]
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "|" not in line:
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not any(cells):
                continue
            if all(re.fullmatch(r"-*", c or "") for c in cells):   # 区切り行
                continue
            if cells[0] in ("評価日", "日付"):                       # ヘッダ行
                continue
            d = _row_dict(cells, cols, a)
            if d:
                rows.append(d)
    print(f"parsed {len(rows)} 行 ← {path}")
    if "--dry-run" in args:
        for d in rows:
            print("  ", {k: d[k] for k in ("eval_date", "media_id", "media", "score",
                                           "judge", "persona", "age_band", "signal")})
        print(f"[dry-run] {len(rows)} 行・書き込みなし")
        return
    created = skipped = 0
    for d in rows:
        if _exists(d["media_id"], d["position"]):
            skipped += 1
            continue
        _req("/pages", {"parent": {"database_id": EVAL_DB}, "properties": build_props_create(d)})
        created += 1
    print(f"✓ bulk: created {created} / skipped(dup) {skipped} → position {a['position'] or '(なし)'}")


def main():
    if not TOKEN:
        sys.exit("NOTION_TOKEN env var is required")
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd, rest = args[0], args[1:]
    {"create": cmd_create, "backfill": cmd_backfill, "bulk": cmd_bulk,
     "find": cmd_find, "list": cmd_list}.get(cmd, lambda a: sys.exit(
        f"unknown command '{cmd}'（create / backfill / bulk / find / list）"))(rest)


if __name__ == "__main__":
    main()
