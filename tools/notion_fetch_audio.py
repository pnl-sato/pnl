#!/usr/bin/env python3
"""Notion ページの添付ファイル（録音 ogg など）を署名付きURL経由でディスクに保存する。

バイナリは呼び出し側（Claude）のコンテキストを通らず、Notion→ディスクへ直接落ちる。

使い方:
    NOTION_TOKEN=ntn_xxx python3 tools/notion_fetch_audio.py <page_or_block_id> [出力ディレクトリ]

挙動:
    - 指定 page/block 配下の children を辿り、type が file / audio / pdf / image の
      ブロックを見つけて、その署名付きURLからダウンロードする。
    - 保存先は既定 /tmp。保存したパスを1行ずつ標準出力に出す（後段スクリプトが受け取る）。
"""
import json
import os
import sys
import urllib.request
import urllib.error

TOKEN = os.environ.get("NOTION_TOKEN")
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
FILE_TYPES = {"file", "audio", "pdf", "image", "video"}


def api_get(path):
    req = urllib.request.Request(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": VERSION},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"[notion api {path}] HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")


def extract_url_and_name(block):
    """file/audio などのブロックから署名URLとファイル名を取り出す。"""
    btype = block.get("type")
    node = block.get(btype, {})
    inner = node.get("file") or node.get("external") or {}
    url = inner.get("url")
    if not url:
        return None, None
    # 名前: caption か URL のパス末尾
    name = None
    cap = node.get("caption") or []
    if cap and cap[0].get("plain_text"):
        name = cap[0]["plain_text"]
    if not name:
        name = url.split("?")[0].rstrip("/").split("/")[-1] or f"{block['id']}.bin"
    return url, name


def walk(block_id, out_dir, depth=0):
    saved = []
    cursor = None
    while True:
        q = f"/blocks/{block_id}/children?page_size=100"
        if cursor:
            q += f"&start_cursor={cursor}"
        data = api_get(q)
        for blk in data.get("results", []):
            if blk.get("type") in FILE_TYPES:
                url, name = extract_url_and_name(blk)
                if url:
                    dest = os.path.join(out_dir, name)
                    urllib.request.urlretrieve(url, dest)
                    size = os.path.getsize(dest)
                    print(dest)
                    sys.stderr.write(f"  saved {dest} ({size} bytes)\n")
                    saved.append(dest)
            if blk.get("has_children") and depth < 3:
                saved += walk(blk["id"], out_dir, depth + 1)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return saved


def main():
    if not TOKEN:
        sys.exit("環境変数 NOTION_TOKEN が未設定です。")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    block_id = sys.argv[1].replace("-", "")
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "/tmp"
    os.makedirs(out_dir, exist_ok=True)
    saved = walk(block_id, out_dir)
    if not saved:
        sys.exit("添付ファイルが見つかりませんでした（インテグレーションがページに共有されているか確認）。")


if __name__ == "__main__":
    main()
