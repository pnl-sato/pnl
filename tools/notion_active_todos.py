#!/usr/bin/env python3
"""Notion ToDo DB から「未完（ステータス≠完了）」のタスクだけを取得して一覧表示する。

なぜこれが要るか:
    ToDo を notion-search（件名のセマンティック検索）で拾うと、ステータスが
    見えず完了済みが混ざる。その結果、完了タスクを「残タスク」として誤提示する
    事故が起きる。本スクリプトは Notion API をステータスでフィルタして、本当に
    未完のものだけを決定的に返す。ToDo の残タスク確認は必ずこれを使うこと。

使い方:
    NOTION_TOKEN=ntn_xxx python3 tools/notion_active_todos.py
    オプション:
      --json                 生の配列を JSON で出力（Claude が再加工する用）
      --work                 私用カテゴリ（PERSONAL_CATEGORIES）を除外して業務だけ出す。
                             朝の業務一覧はこれ推奨（除外方式＝フェイルオープン）
      --category NAME        Category で絞る（include 方式・equals。特定カテゴリだけ見たいとき）
      --exclude-category C   指定カテゴリを除外（does_not_equal）。複数指定可
      --status NAME          ステータスで絞る（例: "進行中"）
      --task-type NAME       TaskType で絞る（例: "NextAction 🚀"）
    既定では Waiting（相手ボール）も含む。相手ボールも未完だが、表示時に区別できるよう
    TaskType でグルーピングして出す。

    朝の業務一覧で --category "Pole&Line"（include 方式）を使うと、Category が空欄の
    ToDo（＝作成時に付け忘れた業務タスク）が黙って落ちる取りこぼしが起きる。--work は
    「私用カテゴリだけ除外」する exclude 方式なので、Category 空欄の業務も拾える
    （空欄は does_not_equal でフェイルオープンに残る）。2026-06 佐藤指示で既定を --work へ。

出力（既定）:
    TaskType ごとにグルーピングし、[優先度][ステータス] タイトル / 開始日 / URL を表示。
    全文や本文ブロックは取得しない（軽量・トークン節約）。
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

TOKEN = os.environ.get("NOTION_TOKEN")
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"

# ToDo データベース（§10）。data source(collection) ID とは別物なので注意。
#   database_id  : 2257d017-b6a0-802b-b90d-fbda463ec16f  ← REST query で使う
#   data source  : 2257d017-b6a0-8026-867c-000bb0969507  ← MCP の collection:// で使う
TODO_DATABASE_ID = "2257d017b6a0802bb90dfbda463ec16f"
DONE_STATUS = "完了"

# 私用カテゴリ（業務一覧から外すもの）。--work 指定でこれらを does_not_equal で除外する。
# 新しい私用カテゴリを追加したらここに足す（業務側にフェイルオープンで漏れ出すのを防ぐ）。
PERSONAL_CATEGORIES = ["Private", "マンション理事会", "Personal Trainer"]

# 表示順
TASKTYPE_ORDER = ["NextAction 🚀", "Inbox 📨", "Waiting ⏳", "Project 🗂️", "Someday 💭", "(未設定)"]
PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2, "": 3}
JST = timezone(timedelta(hours=9), "JST")


def api_post(path, payload):
    req = urllib.request.Request(
        f"{API}{path}", data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": VERSION,
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"[notion todos] HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")


def query_active(task_type=None, category=None, status=None, exclude_categories=None):
    """ステータス≠完了 のページを全件（ページング込み）取得。

    exclude_categories: ここに渡したカテゴリは does_not_equal で除外する。Notion の
    select does_not_equal は「空欄」を一致扱いにしない（＝除外しない）ため、Category
    空欄の業務タスクは残る（フェイルオープン）。--work の挙動の要。
    """
    and_filters = [{"property": "ステータス", "status": {"does_not_equal": DONE_STATUS}}]
    if status:
        and_filters.append({"property": "ステータス", "status": {"equals": status}})
    if task_type:
        and_filters.append({"property": "TaskType", "select": {"equals": task_type}})
    if category:
        and_filters.append({"property": "Category", "select": {"equals": category}})
    for ec in (exclude_categories or []):
        and_filters.append({"property": "Category", "select": {"does_not_equal": ec}})
    payload = {
        "filter": {"and": and_filters},
        "page_size": 100,
    }
    rows, cursor = [], None
    while True:
        if cursor:
            payload["start_cursor"] = cursor
        data = api_post(f"/databases/{TODO_DATABASE_ID}/query", payload)
        rows.extend(data.get("results", []))
        if data.get("has_more"):
            cursor = data.get("next_cursor")
        else:
            break
    return rows


def _title(props):
    arr = props.get("Title", {}).get("title", [])
    return "".join(t.get("plain_text", "") for t in arr).strip() or "(無題)"


def _select(props, name):
    v = props.get(name, {})
    sel = v.get("select") or v.get("status")
    return sel.get("name") if sel else ""


def _date(props, name):
    d = props.get(name, {}).get("date")
    return d.get("start") if d else ""


def extract(page):
    p = page.get("properties", {})
    return {
        "title": _title(p),
        "status": _select(p, "ステータス"),
        "task_type": _select(p, "TaskType") or "(未設定)",
        "priority": _select(p, "優先度"),
        "start": _date(p, "開始時刻"),
        "created": page.get("created_time", ""),
        "url": page.get("url", ""),
    }


def main():
    if not TOKEN:
        sys.exit("環境変数 NOTION_TOKEN が未設定です。")
    args = sys.argv[1:]
    as_json = "--json" in args

    def opt(name):
        if name in args:
            i = args.index(name)
            return args[i + 1] if i + 1 < len(args) else None
        return None

    def opt_all(name):
        vals, i = [], 0
        while i < len(args):
            if args[i] == name and i + 1 < len(args):
                vals.append(args[i + 1])
                i += 2
            else:
                i += 1
        return vals

    task_type = opt("--task-type")
    category = opt("--category")
    status = opt("--status")
    exclude = opt_all("--exclude-category")
    if "--work" in args:
        exclude = list(dict.fromkeys(exclude + PERSONAL_CATEGORIES))

    rows = [extract(pg) for pg in query_active(task_type, category, status, exclude)]
    # 並び替え: TaskType順 → 優先度順 → 開始日(空は後ろ) → 作成日
    rows.sort(key=lambda r: (
        TASKTYPE_ORDER.index(r["task_type"]) if r["task_type"] in TASKTYPE_ORDER else 99,
        PRIORITY_ORDER.get(r["priority"], 3),
        r["start"] or "9999",
        r["created"],
    ))

    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    today = datetime.now(JST).strftime("%Y-%m-%d (%a)")
    print(f"未完ToDo（ステータス≠完了）｜{len(rows)}件  [JST {today}]")
    current = None
    for r in rows:
        if r["task_type"] != current:
            current = r["task_type"]
            print(f"\n## {current}")
        pr = f"[{r['priority'] or '-'}]"
        st = f"[{r['status']}]"
        extra = f"  開始 {r['start']}" if r["start"] else ""
        print(f"- {pr}{st} {r['title']}{extra}\n    {r['url']}")


if __name__ == "__main__":
    main()
