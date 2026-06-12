#!/usr/bin/env python3
"""スカウト送信ログDB に「送信」を1行追加する（going-forward の記録口）。

背景:
    2026-06-12 にスカウトの送信記録を累計カウンタ（スカウトDB.送信数）から
    日付つきログ（スカウト送信ログDB）へ移行した。以後、その日に打った分は
    本スクリプトで dated な行として記録する（スカウトDB.送信数 は手で増やさない＝凍結。
    累計は 送信数（ログ集計）rollup が自動算出、今週分は scout_snapshot.py が日付で出す）。

使い方:
    # 使用中テンプレの一覧（スカウト文の page_id を確認）
    NOTION_TOKEN=ntn_xxx python3 tools/scout_log.py --list [--grep 役員直下]

    # 送信を記録（テンプレ名の部分一致が一意ならそれで指定可）
    python3 tools/scout_log.py --template "役員直下" --count 12 [--date today] [--media Linkedin] [--memo "..."]
    # 曖昧さ回避や確実性重視なら scout 文の page_id を直接指定
    python3 tools/scout_log.py --scout-id <page_id> --count 12 --media Bizreach

    オプション:
      --date    送信日（既定=JST本日。YYYY-MM-DD / today / yesterday）
      --media   媒体（Linkedin/Bizreach/dodaX/eight/リクナビHRTech/SF（Owner）/SF（Others）/YOUTRUST）
      --memo    メモ
      --grep    --list の絞り込み（テンプレ名の部分一致）

注意: --template は使用中テンプレ名の部分一致で解決し、複数該当なら候補を出して中断する。
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta, date

TOKEN = os.environ.get("NOTION_TOKEN")
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
JST = timezone(timedelta(hours=9))
SCOUT_DB = "2597d017b6a0808ea499c4ec941d2a96"
LOG_DB = "c8f02e52eb7940928227c94544f5138b"


def _req(path, payload=None, method="POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": VERSION,
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def _formula_str(pr, name):
    v = pr.get(name, {})
    return v.get("formula", {}).get("string") if v.get("type") == "formula" else None


def used_templates():
    out, cursor = [], None
    while True:
        payload = {"filter": {"property": "使用中", "checkbox": {"equals": True}}, "page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        res = _req(f"/databases/{SCOUT_DB}/query", payload)
        for p in res["results"]:
            pr = p["properties"]
            out.append({
                "id": p["id"],
                "name": _formula_str(pr, "テンプレート名") or "(no name)",
                "media": [m["name"] for m in pr.get("DB", {}).get("multi_select", [])],
                "total": pr.get("送信数（ログ集計）", {}).get("rollup", {}).get("number") or 0,
            })
        if res.get("has_more"):
            cursor = res["next_cursor"]
        else:
            break
    return out


def _opt(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args else default


def resolve_date(s):
    if s in (None, "today"):
        return datetime.now(JST).date()
    if s == "yesterday":
        return datetime.now(JST).date() - timedelta(days=1)
    return date.fromisoformat(s)


def main():
    if not TOKEN:
        sys.exit("NOTION_TOKEN env var is required")
    args = sys.argv[1:]

    if "--list" in args:
        grep = _opt(args, "--grep")
        for t in sorted(used_templates(), key=lambda x: x["name"]):
            if grep and grep not in t["name"]:
                continue
            media = "/".join(t["media"]) or "-"
            print(f"  累計{int(t['total']):>5} | {t['id']} | {t['name']} [{media}]")
        return

    count = _opt(args, "--count")
    if count is None:
        sys.exit("--count is required (送信通数)")
    count = int(count)
    d = resolve_date(_opt(args, "--date"))
    media = _opt(args, "--media")
    memo = _opt(args, "--memo")

    scout_id = _opt(args, "--scout-id")
    name = None
    if not scout_id:
        tmpl = _opt(args, "--template")
        if not tmpl:
            sys.exit("--scout-id か --template のどちらかが必要")
        hits = [t for t in used_templates() if tmpl in t["name"]]
        if len(hits) == 0:
            sys.exit(f"テンプレ名に '{tmpl}' を含む使用中行が見つからない（--list で確認）")
        if len(hits) > 1:
            print(f"複数該当（{len(hits)}件）。--scout-id で一意に指定するか --media で絞ってください：")
            for t in hits:
                print(f"  {t['id']} | {t['name']} [{'/'.join(t['media']) or '-'}]")
            sys.exit(1)
        scout_id = hits[0]["id"]
        name = hits[0]["name"]

    props = {
        "件名": {"title": [{"text": {"content": f"{d.isoformat()} {name or scout_id} {count}通"}}]},
        "日付": {"date": {"start": d.isoformat()}},
        "スカウト文": {"relation": [{"id": scout_id}]},
        "通数": {"number": count},
        "種別": {"select": {"name": "送信"}},
    }
    if media:
        props["媒体"] = {"multi_select": [{"name": media}]}
    if memo:
        props["メモ"] = {"rich_text": [{"text": {"content": memo}}]}

    _req("/pages", {"parent": {"database_id": LOG_DB}, "properties": props})
    print(f"✓ 記録: {d.isoformat()} / {name or scout_id} / {count}通" + (f" / {media}" if media else ""))


if __name__ == "__main__":
    main()
