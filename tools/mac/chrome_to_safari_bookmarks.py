#!/usr/bin/env python3
"""Chrome → Safari ブックマーク一方向同期（Chrome が正）。

Chrome の Bookmarks(JSON) を読み、Safari の ~/Library/Safari/Bookmarks.plist に
書き出す。双方向ではなく Chrome を正とした上書き同期なので衝突解決は不要。

設計メモ:
- Safari は「過去に1回起動して Bookmarks.plist が出来ている」必要がある。
- 書き込む瞬間に Safari が起動中だと、終了時に plist を上書きされて反映が消える。
  そのため起動中は既定で中断する（--quit-safari で自動終了、--force で無視）。
- 書き込み前に既存 plist をタイムスタンプ付きでバックアップする（巻き戻し可能）。
- Safari の Reading List（com.apple.ReadingList）はブックマークではないので温存する。
- 既定は「ブックマークバー＝Chrome の bookmark_bar」「その他＝folder にまとめて root へ」
  の全置換。Safari だけに手動で作ったブックマークは消える点に注意（v1 の割り切り）。

実行は佐藤の Mac 上（VSCode/CLI 版 Claude Code）限定。Web 版からは実行できない。
依存は標準ライブラリのみ（plistlib）。
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Chrome の各ノードを Safari の UUID に決定的に対応づけるための名前空間
# （再実行で UUID が無駄に変わらないよう uuid5 で安定化する）
_UUID_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00cf4fc964ff")

DEFAULT_CHROME = (
    Path.home()
    / "Library/Application Support/Google/Chrome/Default/Bookmarks"
)
DEFAULT_SAFARI = Path.home() / "Library/Safari/Bookmarks.plist"


def stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(_UUID_NS, "/".join(parts))).upper()


def chrome_node_to_safari(node: dict, path: str) -> dict | None:
    """Chrome のブックマークノード(dict)を Safari plist 形式の dict に変換。"""
    ntype = node.get("type")
    name = node.get("name", "")
    if ntype == "url":
        url = node.get("url", "")
        if not url:
            return None
        return {
            "WebBookmarkType": "WebBookmarkTypeLeaf",
            "WebBookmarkUUID": stable_uuid("leaf", path, name, url),
            "URLString": url,
            "URIDictionary": {"title": name},
        }
    if ntype == "folder":
        children = []
        for child in node.get("children", []):
            converted = chrome_node_to_safari(child, f"{path}/{name}")
            if converted is not None:
                children.append(converted)
        return {
            "WebBookmarkType": "WebBookmarkTypeList",
            "WebBookmarkUUID": stable_uuid("list", path, name),
            "Title": name,
            "Children": children,
        }
    return None


def build_children_from_chrome_folder(folder: dict, path: str) -> list[dict]:
    out = []
    for child in folder.get("children", []):
        converted = chrome_node_to_safari(child, path)
        if converted is not None:
            out.append(converted)
    return out


def count_leaves(node: dict) -> int:
    if node.get("WebBookmarkType") == "WebBookmarkTypeLeaf":
        return 1
    return sum(count_leaves(c) for c in node.get("Children", []))


def safari_is_running() -> bool:
    return (
        subprocess.run(
            ["pgrep", "-x", "Safari"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def quit_safari() -> None:
    subprocess.run(
        ["osascript", "-e", 'tell application "Safari" to quit'],
        check=False,
    )


def load_existing_reading_list(safari_path: Path) -> dict | None:
    """既存 Safari plist から Reading List ノードを取り出して温存用に返す。"""
    if not safari_path.exists():
        return None
    try:
        with safari_path.open("rb") as f:
            data = plistlib.load(f)
    except Exception:
        return None
    for child in data.get("Children", []):
        if child.get("Title") == "com.apple.ReadingList":
            return child
    return None


def build_safari_plist(
    chrome_data: dict,
    *,
    keep_reading_list: dict | None,
    other_folder_name: str,
) -> tuple[dict, dict]:
    """Chrome データから Safari plist の dict を組み立てる。統計も返す。"""
    roots = chrome_data.get("roots", {})

    # ブックマークバー
    bar = roots.get("bookmark_bar", {})
    bar_node = {
        "WebBookmarkType": "WebBookmarkTypeList",
        "WebBookmarkUUID": stable_uuid("list", "BookmarksBar"),
        "Title": "BookmarksBar",
        "Children": build_children_from_chrome_folder(bar, "BookmarksBar"),
    }

    children: list[dict] = [bar_node]

    # Reading List は温存（ブックマークではないため）
    if keep_reading_list is not None:
        children.append(keep_reading_list)

    # その他（other / synced）は1つのフォルダにまとめて root 直下へ
    other_children: list[dict] = []
    for key in ("other", "synced"):
        folder = roots.get(key)
        if folder:
            other_children.extend(
                build_children_from_chrome_folder(folder, f"Other/{key}")
            )
    if other_children:
        children.append(
            {
                "WebBookmarkType": "WebBookmarkTypeList",
                "WebBookmarkUUID": stable_uuid("list", "OtherBookmarks"),
                "Title": other_folder_name,
                "Children": other_children,
            }
        )

    plist = {
        "WebBookmarkFileVersion": 1,
        "WebBookmarkType": "WebBookmarkTypeList",
        "WebBookmarkUUID": stable_uuid("root"),
        "Children": children,
    }

    stats = {
        "bar_bookmarks": sum(count_leaves(c) for c in bar_node["Children"]),
        "other_bookmarks": sum(count_leaves(c) for c in other_children),
    }
    return plist, stats


def backup_safari(safari_path: Path) -> Path | None:
    if not safari_path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = safari_path.with_name(f"Bookmarks.plist.bak-{ts}")
    shutil.copy2(safari_path, backup)
    return backup


def prune_backups(safari_path: Path, keep: int) -> None:
    backups = sorted(safari_path.parent.glob("Bookmarks.plist.bak-*"))
    for old in backups[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--chrome-bookmarks", type=Path, default=DEFAULT_CHROME,
                   help=f"Chrome の Bookmarks JSON（既定: {DEFAULT_CHROME}）")
    p.add_argument("--safari-bookmarks", type=Path, default=DEFAULT_SAFARI,
                   help=f"Safari の Bookmarks.plist（既定: {DEFAULT_SAFARI}）")
    p.add_argument("--other-folder-name", default="Other Bookmarks (Chrome)",
                   help="Chrome の other/synced をまとめる Safari フォルダ名")
    p.add_argument("--quit-safari", action="store_true",
                   help="Safari が起動中なら自動で終了させてから書き込む")
    p.add_argument("--force", action="store_true",
                   help="Safari が起動中でも構わず書き込む（非推奨）")
    p.add_argument("--keep-backups", type=int, default=10,
                   help="残すバックアップ世代数（0で無制限。既定: 10）")
    p.add_argument("--dry-run", action="store_true",
                   help="書き込まず、件数と差分の概要だけ表示する")
    args = p.parse_args()

    chrome_path = args.chrome_bookmarks.expanduser()
    safari_path = args.safari_bookmarks.expanduser()

    if not chrome_path.exists():
        print(f"[ERROR] Chrome の Bookmarks が見つかりません: {chrome_path}",
              file=sys.stderr)
        return 2

    # Safari が一度も起動されていないと plist が無い → 初期化を促す
    if not safari_path.exists():
        print(f"[ERROR] Safari の Bookmarks.plist が見つかりません: {safari_path}\n"
              "        Safari を一度起動して終了し、ファイルを初期化してください。",
              file=sys.stderr)
        return 2

    with chrome_path.open("r", encoding="utf-8") as f:
        chrome_data = json.load(f)

    reading_list = load_existing_reading_list(safari_path)
    plist, stats = build_safari_plist(
        chrome_data,
        keep_reading_list=reading_list,
        other_folder_name=args.other_folder_name,
    )

    total = stats["bar_bookmarks"] + stats["other_bookmarks"]
    print(f"Chrome: {chrome_path}")
    print(f"Safari: {safari_path}")
    print(f"  ブックマークバー: {stats['bar_bookmarks']} 件")
    print(f"  その他           : {stats['other_bookmarks']} 件"
          + (" → '" + args.other_folder_name + "'" if stats['other_bookmarks'] else ""))
    print(f"  Reading List     : {'温存' if reading_list else 'なし'}")
    print(f"  合計             : {total} 件")

    if args.dry_run:
        print("[dry-run] 書き込みは行いませんでした。")
        return 0

    if safari_is_running():
        if args.quit_safari:
            print("Safari が起動中のため終了させます…")
            quit_safari()
            # 終了完了を簡易に待つ
            for _ in range(20):
                if not safari_is_running():
                    break
                subprocess.run(["sleep", "0.5"], check=False)
        elif not args.force:
            print("[ABORT] Safari が起動中です。終了してから再実行するか、"
                  "--quit-safari / --force を付けてください。", file=sys.stderr)
            return 3

    backup = backup_safari(safari_path)
    if backup:
        print(f"バックアップ: {backup}")
        prune_backups(safari_path, args.keep_backups)

    with safari_path.open("wb") as f:
        plistlib.dump(plist, f, fmt=plistlib.FMT_BINARY)
    print(f"[OK] Safari に {total} 件を書き込みました。"
          "（次回 Safari 起動時に反映されます）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
