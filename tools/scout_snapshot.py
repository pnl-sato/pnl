#!/usr/bin/env python3
"""Notion スカウト送信ログDB から、注力セグメントの送信数を日付解決して集計する。

背景（なぜログDBか）:
    スカウトDBの `送信数` は単調増加の累計1個だけで、「今週打った分」を後から
    復元できなかった（週初スナップショットを覚えていないと壊れる）。2026-06-12 に
    「スカウト送信ログ」子DB（1行=日付・スカウト文・通数）へ移行し、累計は
    `送信数（ログ集計）` rollup に置換。今日/今週/今月は日付で絞って出す。
    旧累計は同日に種別=繰越の1行として移行済（cutover）。

このスクリプトの役割:
    ログDBを日付バケット（今日 / 今週=月曜起点 / 今月 / 累計）で集計し、
    スカウトDBからテンプレ名・注力フラグ・返信数を引いて結合。注力(注力=YES)
    セグメントを上に表示する。返信率は 返信数 / 累計(繰越含む) で算出。

使い方:
    NOTION_TOKEN=ntn_xxx python3 tools/scout_snapshot.py
    オプション:
      --json        生の集計を JSON 出力
      --all         注力以外も全件表示
      --top N       注力以外の表示件数（既定15）
      --today DATE  「今日」基準日を上書き（既定=JST本日。週/月もこれ基準）

正本: Notion「スカウト送信ログ」DB ＋ スカウトDBの 送信数（ログ集計）rollup。
返信率の判定ルールは agents/scout.md §10。
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

# CLAUDE.md §5 / 2026-06 移行。
SCOUT_DB = "2597d017b6a0808ea499c4ec941d2a96"        # スカウトDB（テンプレ正本）
LOG_DB = "c8f02e52eb7940928227c94544f5138b"          # スカウト送信ログ（送信イベント）


def _post(path, payload):
    req = urllib.request.Request(
        f"{API}{path}", data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": VERSION,
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def _query_all(db, filt=None):
    rows, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if filt:
            payload["filter"] = filt
        if cursor:
            payload["start_cursor"] = cursor
        res = _post(f"/databases/{db}/query", payload)
        rows.extend(res["results"])
        if res.get("has_more"):
            cursor = res["next_cursor"]
        else:
            break
    return rows


def _formula_str(pr, name):
    v = pr.get(name, {})
    return v.get("formula", {}).get("string") if v.get("type") == "formula" else None


def scout_index():
    """スカウト文 page_id -> {name, focus, reply} を作る。"""
    idx = {}
    for p in _query_all(SCOUT_DB, {"property": "使用中", "checkbox": {"equals": True}}):
        pr = p["properties"]
        focus = False
        roll = pr.get("注力", {}).get("rollup", {})
        for a in roll.get("array", []):
            if a.get("type") == "checkbox" and a.get("checkbox"):
                focus = True
        idx[p["id"]] = {
            "name": _formula_str(pr, "テンプレート名") or "(no name)",
            "focus": focus,
            "reply": pr.get("返信数", {}).get("number") or 0,
        }
    return idx


def main():
    if not TOKEN:
        sys.exit("NOTION_TOKEN env var is required")
    args = sys.argv[1:]
    as_json = "--json" in args
    show_all = "--all" in args
    top = None if show_all else 15
    if "--top" in args:
        top = int(args[args.index("--top") + 1])
    if "--today" in args:
        today = date.fromisoformat(args[args.index("--today") + 1])
    else:
        today = datetime.now(JST).date()
    week_start = today - timedelta(days=today.weekday())   # 月曜
    month_start = today.replace(day=1)

    idx = scout_index()

    # テンプレ名単位で集計（同名の複数行＝媒体違いを名寄せ）
    agg = defaultdict(lambda: {"focus": False, "reply": 0,
                               "total": 0, "send": 0, "today": 0, "week": 0, "month": 0})
    # 返信は scoutDB 側（テンプレ名で合算、重複加算しないよう id 単位で集約）
    rep_by_name = defaultdict(int)
    seen = set()
    for sid, meta in idx.items():
        agg[meta["name"]]["focus"] |= meta["focus"]
        if sid not in seen:
            rep_by_name[meta["name"]] += meta["reply"]
            seen.add(sid)

    for p in _query_all(LOG_DB):
        pr = p["properties"]
        rels = pr.get("スカウト文", {}).get("relation", [])
        if not rels:
            continue
        meta = idx.get(rels[0]["id"])
        if not meta:           # 非使用中テンプレのログは無視（注力対象外）
            continue
        name = meta["name"]
        n = pr.get("通数", {}).get("number") or 0
        kind = (pr.get("種別", {}).get("select") or {}).get("name")
        d = (pr.get("日付", {}).get("date") or {}).get("start")
        d = date.fromisoformat(d[:10]) if d else None
        a = agg[name]
        a["total"] += n
        if kind == "繰越":
            continue
        a["send"] += n
        if d and d == today:
            a["today"] += n
        if d and d >= week_start:
            a["week"] += n
        if d and d >= month_start:
            a["month"] += n

    for name, a in agg.items():
        a["reply"] = rep_by_name.get(name, 0)
        a["rate"] = f"{a['reply'] / a['total'] * 100:.1f}%" if a["total"] else "-"

    if as_json:
        print(json.dumps({
            "today": today.isoformat(), "week_start": week_start.isoformat(),
            "month_start": month_start.isoformat(),
            "segments": agg}, ensure_ascii=False, indent=2, default=dict))
        return

    focus = sorted((kv for kv in agg.items() if kv[1]["focus"]), key=lambda x: -x[1]["total"])
    other = sorted((kv for kv in agg.items() if not kv[1]["focus"] and x_total(kv)), key=lambda x: -x[1]["total"])

    print(f"スカウト送信スナップショット  本日 {today} ／ 今週起点(月) {week_start} ／ 今月起点 {month_start}")
    print("（今日/今週/今月＝繰越除く実送信、累計＝繰越含む。正本=スカウト送信ログDB）")
    f_today = sum(a["today"] for _, a in focus)
    f_week = sum(a["week"] for _, a in focus)
    print(f"\n■ 注力セグメント合計：今日 {f_today} ／ 今週 {f_week}")
    print("===== 注力=YES セグメント =====")
    print(f"  {'今日':>4}{'今週':>5}{'今月':>5}{'累計':>6}{'返信':>5} 返信率 | テンプレ")
    for name, a in focus:
        print(f"  {a['today']:>4}{a['week']:>5}{a['month']:>5}{a['total']:>6}{a['reply']:>5} {a['rate']:>6} | {name}")
    label = "全件" if top is None else f"上位{top}"
    print(f"\n===== 注力以外（{label}・累計順） =====")
    for name, a in (other if top is None else other[:top]):
        print(f"  {a['today']:>4}{a['week']:>5}{a['month']:>5}{a['total']:>6}{a['reply']:>5} {a['rate']:>6} | {name}")


def x_total(kv):
    return kv[1]["total"] > 0


if __name__ == "__main__":
    main()
