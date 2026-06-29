#!/usr/bin/env python3
"""スカウト評価ログ → スカウト送信ログ への日次自動集計（ハイブリッド運用の橋渡し）。

背景:
    日々のスカウトは「スカウト評価ログDB」に 1候補=1行（判定/送信日/返信日/結果/ペルソナ等）で
    残す運用に寄せた。一方、週次ノルマ・累計・返信率は従来どおり「スカウト送信ログDB」を
    正本（scout_snapshot.py が読む）として維持する。本スクリプトは両者の二重入力をなくすため、
    評価ログの「送信日が入っている行（＝実際に送った行）」を 日付 × スカウト文(テンプレ) で集計し、
    送信ログへ冪等に upsert する。

送信の定義:
    評価ログで「送信日」が入っている行 = 送信済み。
    （判定B以上は送信デフォルトで送信日を入れる／Cは送った場合のみ送信日を入れる／Dは未送信で空。
      送信日の有無を ground truth にするので、判定値そのものではなくこのフィールドで判定する。）

冪等性:
    生成する送信ログ行は メモ に "eval-agg" マーカーを付ける。再実行時は
    (日付 × スカウト文 × eval-agg) で既存行を探し、あれば通数を更新、なければ新規作成する。
    → 何度流しても二重計上しない。手打ちの scout_log.py 行（マーカー無し）とは混ざらない。

テンプレ未紐付け:
    評価ログ行に スカウト文 のリレーションが無いと、どのテンプレ／セグメントの送信か特定できず
    送信ログに正しく積めない。その場合は集計せず警告で一覧表示する。
    --assign-template <id|名前部分一致> を付けると、対象日の未紐付け送信行に スカウト文 を
    後付けリレーションしてから集計する（評価ログ側も埋まるので次回以降は自動で乗る）。

使い方:
    # まず確認（書き込みなし）
    NOTION_TOKEN=ntn_xxx python3 tools/scout_eval_to_log.py --date today --dry-run
    # 反映
    python3 tools/scout_eval_to_log.py --date 2026-06-15
    # 未紐付け行に役員直下v2を後付けして反映
    python3 tools/scout_eval_to_log.py --date 2026-06-15 --assign-template "［v2］新規事業開発"

    オプション:
      --date            集計対象の送信日（既定=JST本日。YYYY-MM-DD / today / yesterday）。--all で全期間
      --all             送信日を限定せず全期間を集計
      --assign-template 未紐付けの送信行に後付けするテンプレ（スカウト文 page_id か 使用中テンプレ名の部分一致）
      --dry-run         書き込みせず計画だけ表示
"""
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date

TOKEN = os.environ.get("NOTION_TOKEN")
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
JST = timezone(timedelta(hours=9))
SCOUT_DB = "2597d017b6a0808ea499c4ec941d2a96"
LOG_DB = "c8f02e52eb7940928227c94544f5138b"
EVAL_DB = "42e00d7d5c7e4b09aa80d6c1cd4e55bf"
MARKER = "eval-agg"


def _req(path, payload=None, method="POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": VERSION,
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def _opt(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args else default


def resolve_date(s):
    if s in (None, "today"):
        return datetime.now(JST).date()
    if s == "yesterday":
        return datetime.now(JST).date() - timedelta(days=1)
    return date.fromisoformat(s)


def _formula_str(pr, name):
    v = pr.get(name, {})
    return v.get("formula", {}).get("string") if v.get("type") == "formula" else None


def _date_start(pr, name):
    v = pr.get(name) or {}
    return (v.get("date") or {}).get("start") if v.get("type") == "date" else None


def _media_names(pr, name="媒体"):
    v = pr.get(name) or {}
    if v.get("type") == "multi_select":
        return [m["name"] for m in v["multi_select"]]
    if v.get("type") == "select" and v.get("select"):
        return [v["select"]["name"]]
    return []


def _query_all(db, payload):
    out, cursor = [], None
    while True:
        p = dict(payload)
        if cursor:
            p["start_cursor"] = cursor
        res = _req(f"/databases/{db}/query", p)
        out.extend(res["results"])
        if res.get("has_more"):
            cursor = res["next_cursor"]
        else:
            break
    return out


def template_name(scout_id):
    try:
        page = _req(f"/pages/{scout_id}", method="GET")
        return _formula_str(page["properties"], "テンプレート名") or scout_id
    except Exception:
        return scout_id


def used_templates():
    rows = _query_all(SCOUT_DB, {"filter": {"property": "使用中", "checkbox": {"equals": True}}, "page_size": 100})
    out = []
    for p in rows:
        pr = p["properties"]
        out.append({"id": p["id"], "name": _formula_str(pr, "テンプレート名") or "(no name)",
                    "media": [m["name"] for m in pr.get("DB", {}).get("multi_select", [])]})
    return out


def resolve_template(s):
    """page_id か 使用中テンプレ名の部分一致を 1件に解決して page_id を返す。"""
    s = s.strip()
    if len(s.replace("-", "")) == 32 and all(c in "0123456789abcdef-" for c in s.lower()):
        return s
    hits = [t for t in used_templates() if s in t["name"]]
    if len(hits) == 0:
        sys.exit(f"テンプレ名に '{s}' を含む使用中行が見つからない")
    if len(hits) > 1:
        print(f"複数該当（{len(hits)}件）。page_id で一意指定してください：")
        for t in hits:
            print(f"  {t['id']} | {t['name']} [{'/'.join(t['media']) or '-'}]")
        sys.exit(1)
    return hits[0]["id"]


def eval_sent_rows(target_date):
    """評価ログの「送信日あり」行を返す。target_date が None なら全期間。"""
    flt = {"property": "送信日", "date": {"is_not_empty": True}}
    if target_date is not None:
        flt = {"and": [flt, {"property": "送信日", "date": {"equals": target_date.isoformat()}}]}
    rows = _query_all(EVAL_DB, {"filter": flt, "page_size": 100})
    out = []
    for p in rows:
        pr = p["properties"]
        rel = (pr.get("スカウト文") or {}).get("relation") or []
        out.append({
            "id": p["id"],
            "title": next(("".join(x["plain_text"] for x in v["title"])
                           for v in pr.values() if v["type"] == "title"), "?"),
            "send_date": _date_start(pr, "送信日"),
            "scout_id": rel[0]["id"] if rel else None,
            "media": _media_names(pr),
        })
    return out


def find_log_row(d, scout_id):
    """(日付 × スカウト文 × eval-agg マーカー) の既存ログ行を返す（無ければ None）。"""
    res = _req(f"/databases/{LOG_DB}/query", {"filter": {"and": [
        {"property": "日付", "date": {"equals": d}},
        {"property": "スカウト文", "relation": {"contains": scout_id}},
        {"property": "メモ", "rich_text": {"contains": MARKER}},
    ]}, "page_size": 5})
    return res["results"][0] if res["results"] else None


def upsert_log(d, scout_id, count, media, dry):
    name = template_name(scout_id)
    title = f"{d} {name} {count}通"
    existing = find_log_row(d, scout_id)
    memo = f"{MARKER} 自動集計（評価ログ→送信ログ, {datetime.now(JST).date()}実行）"
    props = {
        "件名": {"title": [{"text": {"content": title}}]},
        "日付": {"date": {"start": d}},
        "スカウト文": {"relation": [{"id": scout_id}]},
        "通数": {"number": count},
        "種別": {"select": {"name": "送信"}},
        "メモ": {"rich_text": [{"text": {"content": memo}}]},
    }
    if media:
        props["媒体"] = {"multi_select": [{"name": m} for m in media]}
    action = "update" if existing else "create"
    if dry:
        print(f"  [{action}] {title}" + (f" [{'/'.join(media)}]" if media else ""))
        return
    if existing:
        _req(f"/pages/{existing['id']}", {"properties": props}, method="PATCH")
    else:
        _req("/pages", {"parent": {"database_id": LOG_DB}, "properties": props})
    print(f"  ✓ {action}: {title}" + (f" [{'/'.join(media)}]" if media else ""))


def main():
    if not TOKEN:
        sys.exit("NOTION_TOKEN env var is required")
    args = sys.argv[1:]
    dry = "--dry-run" in args
    target_date = None if "--all" in args else resolve_date(_opt(args, "--date"))
    assign = _opt(args, "--assign-template")

    rows = eval_sent_rows(target_date)
    scope = "全期間" if target_date is None else target_date.isoformat()
    print(f"対象（送信日あり）: {len(rows)}件 / 範囲={scope}" + (" / DRY-RUN" if dry else ""))

    unlinked = [r for r in rows if not r["scout_id"]]
    if unlinked and assign:
        scout_id = resolve_template(assign)
        print(f"\n未紐付け {len(unlinked)}件に スカウト文={template_name(scout_id)} を後付け" + (" (dry)" if dry else ""))
        for r in unlinked:
            if not dry:
                _req(f"/pages/{r['id']}", {"properties": {"スカウト文": {"relation": [{"id": scout_id}]}}}, method="PATCH")
            r["scout_id"] = scout_id
        unlinked = []

    # 集計: (送信日, スカウト文) → 通数 と 媒体集合
    groups = defaultdict(lambda: {"count": 0, "media": set()})
    for r in rows:
        if not r["scout_id"]:
            continue
        g = groups[(r["send_date"], r["scout_id"])]
        g["count"] += 1
        g["media"].update(r["media"])

    if groups:
        print("\n送信ログへ upsert:")
        for (d, sid), g in sorted(groups.items()):
            upsert_log(d, sid, g["count"], sorted(g["media"]), dry)

    if unlinked:
        print(f"\n⚠ スカウト文 未紐付けの送信行 {len(unlinked)}件（集計せずスキップ）:")
        by_date = defaultdict(list)
        for r in unlinked:
            by_date[r["send_date"]].append(r)
        for d, rs in sorted(by_date.items()):
            print(f"  {d}: {len(rs)}件 媒体={'/'.join(sorted({m for r in rs for m in r['media']})) or '-'}"
                  f" 例={', '.join(r['title'] for r in rs[:3])}…")
        print("  → どのテンプレか確定したら --assign-template '<テンプレ名 or page_id>' で後付け＋集計できます。")


if __name__ == "__main__":
    main()
