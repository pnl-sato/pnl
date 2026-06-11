#!/usr/bin/env python3
"""Notion スカウトDB から「使用中=YES」テンプレの累積送信/返信をテンプレ単位で集計する。

なぜこれが要るか:
    朝のTODO生成（agents/daily-todo.md §2）で「今日のスカウト通数」を出すには、
    各注力セグメントの累積送信数が要る。スカウトDBは媒体別に行が分散している
    （1テンプレが Linkedin/Bizreach… で複数行）ため、テンプレ名で名寄せして
    合算しないと正しい累積が出ない。本スクリプトはそれを決定的に行い、
    注力(注力=YES)セグメントを上に、その他の送信実績も合わせて返す。

    日次運用（baseline 記録）:
        - 毎朝これを実行し、注力セグメントの累積を DailyLog にスナップショットする。
        - 月曜は当日累積を「週初ベースライン」として記録（今週送信済＝現在累積−月曜値）。
        - これを怠ると週次の送信ペースが追えなくなる（2026-06 に baseline 欠落で発生）。

使い方:
    NOTION_TOKEN=ntn_xxx python3 tools/scout_snapshot.py
    オプション:
      --json     生の集計を JSON で出力（Claude が再加工する用）
      --all      注力以外も含め、送信実績のある全テンプレを表示
      --top N    注力以外の表示件数（既定15、--all 指定時は無制限）

正本: Notion スカウトDB の各テンプレ `送信数`／`返信数`。返信率の判定ルールは agents/scout.md §10。
"""
import json
import os
import sys
import urllib.request
from collections import defaultdict

TOKEN = os.environ.get("NOTION_TOKEN")
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"

# スカウトDB（CLAUDE.md §5）。
#   database_id : 2597d017-b6a0-808e-a499-c4ec941d2a96  ← REST query で使う
#   data source : 2597d017-b6a0-801b-8185-000ba4b9661e  ← MCP の collection:// で使う
SCOUT_DATABASE_ID = "2597d017b6a0808ea499c4ec941d2a96"


def _post(path, payload):
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        },
    )
    return json.load(urllib.request.urlopen(req))


def _text(pr, name):
    v = pr.get(name, {})
    t = v.get("type")
    if t == "formula":
        f = v["formula"]
        return f.get("string") or f.get("number")
    if t == "title":
        return "".join(x["plain_text"] for x in v.get("title", []))
    if t == "rich_text":
        return "".join(x["plain_text"] for x in v.get("rich_text", []))
    return None


def _is_focus(pr):
    v = pr.get("注力", {})
    if v.get("type") == "rollup":
        for a in v["rollup"].get("array", []):
            if a.get("type") == "checkbox" and a.get("checkbox"):
                return True
    return False


def fetch_aggregated():
    agg = defaultdict(lambda: {"send": 0, "reply": 0, "focus": False})
    rows = 0
    cursor = None
    while True:
        payload = {
            "filter": {"property": "使用中", "checkbox": {"equals": True}},
            "page_size": 100,
        }
        if cursor:
            payload["start_cursor"] = cursor
        res = _post(f"/databases/{SCOUT_DATABASE_ID}/query", payload)
        for p in res["results"]:
            pr = p["properties"]
            rows += 1
            name = _text(pr, "テンプレート名") or _text(pr, "タイトル") or "(no name)"
            agg[name]["send"] += pr.get("送信数", {}).get("number") or 0
            agg[name]["reply"] += pr.get("返信数", {}).get("number") or 0
            if _is_focus(pr):
                agg[name]["focus"] = True
        if res.get("has_more"):
            cursor = res["next_cursor"]
        else:
            break
    return agg, rows


def _rate(d):
    return f"{d['reply'] / d['send'] * 100:.1f}%" if d["send"] else "-"


def main():
    if not TOKEN:
        sys.exit("NOTION_TOKEN env var is required")
    args = sys.argv[1:]
    as_json = "--json" in args
    show_all = "--all" in args
    top = None if show_all else 15
    if "--top" in args:
        top = int(args[args.index("--top") + 1])

    agg, rows = fetch_aggregated()

    if as_json:
        out = {n: {**d, "rate": _rate(d)} for n, d in agg.items()}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    focus = sorted(
        ((n, d) for n, d in agg.items() if d["focus"]),
        key=lambda x: -x[1]["send"],
    )
    other = sorted(
        ((n, d) for n, d in agg.items() if not d["focus"] and d["send"] > 0),
        key=lambda x: -x[1]["send"],
    )

    print(f"スカウト累積スナップショット（使用中行 {rows}／テンプレ {len(agg)}）")
    print("\n===== 注力=YES セグメント（日次ベースライン対象） =====")
    for n, d in focus:
        print(f"  送信{d['send']:>5} 返信{d['reply']:>4} (返信率{_rate(d):>6}) | {n}")
    label = "全件" if top is None else f"上位{top}"
    print(f"\n===== 注力以外で送信実績あり（{label}） =====")
    for n, d in (other if top is None else other[:top]):
        print(f"  送信{d['send']:>5} 返信{d['reply']:>4} (返信率{_rate(d):>6}) | {n}")


if __name__ == "__main__":
    main()
