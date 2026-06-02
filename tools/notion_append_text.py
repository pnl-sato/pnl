#!/usr/bin/env python3
"""テキストファイルの中身を Notion ページに段落ブロックとして追記する。

全文はディスクから読んで直接 Notion API に POST するため、呼び出し側（Claude）の
コンテキストを通らない。

使い方:
    NOTION_TOKEN=ntn_xxx python3 tools/notion_append_text.py <page_id> <テキストファイル> [見出し]

Notion の制約に合わせて自動処理:
    - 1 rich_text あたり 2000 文字でチャンク分割
    - 1リクエスト 100 ブロックまでに分割して複数回 append
"""
import json
import os
import sys
import urllib.request
import urllib.error

TOKEN = os.environ.get("NOTION_TOKEN")
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
MAX_CHARS = 1900          # 2000 制限に余裕
MAX_BLOCKS_PER_REQ = 90   # 100 制限に余裕


def api_patch(path, payload):
    req = urllib.request.Request(
        f"{API}{path}", data=json.dumps(payload).encode(), method="PATCH",
        headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": VERSION,
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"[notion append] HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")


def para(text):
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def heading(text):
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def chunk(text):
    """段落（改行区切り）を保ちつつ 2000 字制限内に分割。"""
    blocks = []
    for line in text.split("\n"):
        if line == "":
            continue
        while len(line) > MAX_CHARS:
            blocks.append(para(line[:MAX_CHARS]))
            line = line[MAX_CHARS:]
        blocks.append(para(line))
    return blocks


def main():
    if not TOKEN:
        sys.exit("環境変数 NOTION_TOKEN が未設定です。")
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    page_id = sys.argv[1].replace("-", "")
    with open(sys.argv[2], encoding="utf-8") as f:
        text = f.read()
    blocks = []
    if len(sys.argv) > 3:
        blocks.append(heading(sys.argv[3]))
    blocks += chunk(text)

    for i in range(0, len(blocks), MAX_BLOCKS_PER_REQ):
        api_patch(f"/blocks/{page_id}/children",
                  {"children": blocks[i:i + MAX_BLOCKS_PER_REQ]})
    sys.stderr.write(f"  appended {len(blocks)} blocks to page {page_id}\n")
    print(f"https://www.notion.so/{page_id}")


if __name__ == "__main__":
    main()
