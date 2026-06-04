#!/usr/bin/env python3
"""Notion ページのテキスト全文をディスク（標準出力）に書き出す。

ページ配下のブロックを再帰取得し、rich_text を持つブロックの平文を改行区切りで
出力する。文字起こしページ等から議事録生成の入力を作る用途で、全文を呼び出し側
（Claude）のコンテキストに通さずに済ませるためのヘルパー。

使い方:
    NOTION_TOKEN=ntn_xxx python3 tools/notion_fetch_text.py <page_id> > out.txt
"""
import json
import os
import sys
import urllib.request
import urllib.error

TOKEN = os.environ.get("NOTION_TOKEN")
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"


def api_get(path):
    req = urllib.request.Request(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": VERSION},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"[notion fetch] HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")


def rich_to_text(rich):
    return "".join(r.get("plain_text", "") for r in rich)


def walk(block_id, out):
    cursor = None
    while True:
        q = f"/blocks/{block_id}/children?page_size=100"
        if cursor:
            q += f"&start_cursor={cursor}"
        data = api_get(q)
        for b in data.get("results", []):
            t = b.get("type")
            rich = b.get(t, {}).get("rich_text")
            if rich:
                line = rich_to_text(rich)
                if line.strip():
                    out.append(line)
            if b.get("has_children") and t not in ("child_page", "child_database"):
                walk(b["id"], out)
        if data.get("has_more"):
            cursor = data.get("next_cursor")
        else:
            break


def main():
    if not TOKEN:
        sys.exit("環境変数 NOTION_TOKEN が未設定です。")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    page_id = sys.argv[1].replace("-", "")
    out = []
    walk(page_id, out)
    sys.stdout.write("\n".join(out) + "\n")
    sys.stderr.write(f"  fetched {len(out)} text blocks from page {page_id}\n")


if __name__ == "__main__":
    main()
