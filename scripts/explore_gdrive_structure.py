"""
Google Drive 構造探索スクリプト

【概要】
Google Drive のフォルダ・ファイル構造を再帰的に探索し、
google_drive_structure.md としてドキュメント化する。

【セットアップ】
1. Google Cloud Console でサービスアカウントを作成
   https://console.cloud.google.com/
   → IAMと管理 → サービスアカウント → 作成
   → キー → JSONで追加 → credentials/gdrive_service_account.json として保存

2. Google Drive API を有効化
   → APIとサービス → ライブラリ → Google Drive API → 有効にする

3. 対象フォルダをサービスアカウントと共有（閲覧者権限でOK）
   サービスアカウントのメールアドレスをフォルダの共有設定に追加

4. .env に設定追加
   GDRIVE_ROOT_FOLDER_ID=xxxxxxxxxxxxx  # 探索起点フォルダのID

【フォルダIDの取得方法】
  Google Drive でフォルダを開いた時の URL:
  https://drive.google.com/drive/folders/{この部分がフォルダID}

【実行】
  python scripts/explore_gdrive_structure.py
  python scripts/explore_gdrive_structure.py --depth 3      # 深さ制限
  python scripts/explore_gdrive_structure.py --no-files     # フォルダのみ
  python scripts/explore_gdrive_structure.py --folder-id xxx  # 特定フォルダを探索
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ─── 設定 ─────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

CREDENTIALS_FILE = Path(__file__).parent.parent / "credentials" / "gdrive_service_account.json"
ROOT_FOLDER_ID   = os.environ.get("GDRIVE_ROOT_FOLDER_ID", "")
OUTPUT_FILE      = Path(__file__).parent.parent / "google_drive_structure.md"

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

JST = ZoneInfo("Asia/Tokyo")

# Google Drive の MIME タイプ
FOLDER_MIME = "application/vnd.google-apps.folder"
GDOC_MIME   = "application/vnd.google-apps.document"
GSHEET_MIME = "application/vnd.google-apps.spreadsheet"
GSLIDE_MIME = "application/vnd.google-apps.presentation"

MIME_LABELS = {
    GDOC_MIME:   "Googleドキュメント",
    GSHEET_MIME: "Googleスプレッドシート",
    GSLIDE_MIME: "Googleスライド",
    "application/pdf": "PDF",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Excel",
}


# ─── Google Drive クライアント ─────────────────────────────────────────────────

def build_service():
    if not CREDENTIALS_FILE.exists():
        print(f"❌ 認証ファイルが見つかりません: {CREDENTIALS_FILE}")
        print("   セットアップ手順を確認してください（スクリプト冒頭のコメント参照）")
        sys.exit(1)

    creds = service_account.Credentials.from_service_account_file(
        str(CREDENTIALS_FILE), scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


# ─── フォルダ探索 ──────────────────────────────────────────────────────────────

def list_children(service, folder_id: str) -> list[dict]:
    """フォルダ直下のファイル・フォルダ一覧を取得（フォルダ→ファイルの順でソート）"""
    results = []
    page_token = None

    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType, modifiedTime, size, webViewLink)",
            orderBy="folder,name",
            pageSize=200,
            pageToken=page_token,
        ).execute()

        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return results


def get_folder_info(service, folder_id: str) -> dict | None:
    """フォルダの情報を取得"""
    try:
        return service.files().get(
            fileId=folder_id,
            fields="id, name, mimeType, webViewLink"
        ).execute()
    except Exception:
        return None


def explore_folder(
    service,
    folder_id: str,
    depth: int = 0,
    max_depth: int | None = None,
    include_files: bool = True,
    stats: dict | None = None,
) -> list[dict]:
    """
    フォルダを再帰的に探索し、ツリー構造のリストを返す。

    戻り値の各要素:
      {
        "depth": int,
        "name": str,
        "id": str,
        "mime": str,
        "is_folder": bool,
        "link": str,
        "modified": str,  # ファイルのみ
        "size": int | None,
      }
    """
    if stats is None:
        stats = {"folders": 0, "files": 0}

    if max_depth is not None and depth > max_depth:
        return []

    children = list_children(service, folder_id)
    nodes = []

    for item in children:
        is_folder = item["mimeType"] == FOLDER_MIME

        if not include_files and not is_folder:
            continue

        node = {
            "depth": depth,
            "name": item["name"],
            "id": item["id"],
            "mime": item["mimeType"],
            "is_folder": is_folder,
            "link": item.get("webViewLink", ""),
            "modified": item.get("modifiedTime", "")[:10] if not is_folder else "",
            "size": item.get("size"),
        }
        nodes.append(node)

        if is_folder:
            stats["folders"] += 1
            sub_nodes = explore_folder(
                service, item["id"],
                depth=depth + 1,
                max_depth=max_depth,
                include_files=include_files,
                stats=stats,
            )
            nodes.extend(sub_nodes)
        else:
            stats["files"] += 1

    return nodes, stats


# ─── Markdown 生成 ─────────────────────────────────────────────────────────────

def mime_icon(mime: str, is_folder: bool) -> str:
    if is_folder:
        return "📁"
    icons = {
        GDOC_MIME:   "📄",
        GSHEET_MIME: "📊",
        GSLIDE_MIME: "📑",
        "application/pdf": "📕",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "📝",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "📈",
    }
    return icons.get(mime, "📎")


def format_size(size: str | None) -> str:
    if not size:
        return ""
    n = int(size)
    if n < 1024:
        return f"{n}B"
    elif n < 1024 ** 2:
        return f"{n // 1024}KB"
    else:
        return f"{n // 1024 ** 2}MB"


def build_markdown(
    root_info: dict,
    nodes: list[dict],
    stats: dict,
    root_folder_id: str,
    generated_at: str,
) -> str:
    lines = [
        "# Google Drive 構造ドキュメント",
        "",
        "本ドキュメントは P&L（Pole & Line）の Google Drive フォルダ構造を記録したものです。",
        "",
        "---",
        "",
        "## 概要",
        "",
        f"| 項目 | 値 |",
        f"|------|-----|",
        f"| ルートフォルダ | [{root_info.get('name', root_folder_id)}]({root_info.get('webViewLink', '')}) |",
        f"| フォルダID | `{root_folder_id}` |",
        f"| フォルダ数 | {stats['folders']} |",
        f"| ファイル数 | {stats['files']} |",
        f"| 最終更新 | {generated_at} |",
        "",
        "---",
        "",
        "## フォルダ・ファイル構造",
        "",
    ]

    for node in nodes:
        indent = "  " * node["depth"]
        icon = mime_icon(node["mime"], node["is_folder"])
        name = node["name"]
        link = node["link"]
        mime_label = MIME_LABELS.get(node["mime"], "") if not node["is_folder"] else ""
        modified = f" _{node['modified']}_" if node["modified"] else ""
        size_str = f" `{format_size(node['size'])}`" if node["size"] else ""
        type_str = f" `{mime_label}`" if mime_label else ""

        if link:
            entry = f"{indent}- {icon} [{name}]({link}){type_str}{modified}{size_str}"
        else:
            entry = f"{indent}- {icon} {name}{type_str}{modified}{size_str}"

        lines.append(entry)

    lines.extend([
        "",
        "---",
        "",
        "## ファイルタイプ凡例",
        "",
        "| アイコン | 種類 |",
        "|---------|------|",
        "| 📁 | フォルダ |",
        "| 📄 | Googleドキュメント |",
        "| 📊 | Googleスプレッドシート |",
        "| 📑 | Googleスライド |",
        "| 📕 | PDF |",
        "| 📝 | Word |",
        "| 📈 | Excel |",
        "| 📎 | その他 |",
        "",
        f"*最終更新: {generated_at}*",
    ])

    return "\n".join(lines)


# ─── メイン ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Google Drive 構造探索ツール")
    parser.add_argument("--depth",     type=int, default=None, help="探索深さの上限（デフォルト: 無制限）")
    parser.add_argument("--no-files",  action="store_true",    help="フォルダのみ表示（ファイルをスキップ）")
    parser.add_argument("--folder-id", type=str, default=None, help="探索起点フォルダID（省略時は.envのGDRIVE_ROOT_FOLDER_IDを使用）")
    parser.add_argument("--output",    type=str, default=None, help="出力ファイルパス（省略時はgoogle_drive_structure.md）")
    args = parser.parse_args()

    folder_id = args.folder_id or ROOT_FOLDER_ID
    if not folder_id:
        print("❌ フォルダIDが指定されていません。")
        print("   .env に GDRIVE_ROOT_FOLDER_ID=xxx を設定するか、--folder-id で指定してください。")
        sys.exit(1)

    output_path = Path(args.output) if args.output else OUTPUT_FILE

    print("🔑 Google Drive に接続中...")
    service = build_service()

    print(f"📂 フォルダを探索中: {folder_id}")
    root_info = get_folder_info(service, folder_id) or {}

    nodes, stats = explore_folder(
        service, folder_id,
        max_depth=args.depth,
        include_files=not args.no_files,
    )

    generated_at = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M")
    md = build_markdown(root_info, nodes, stats, folder_id, generated_at)

    output_path.write_text(md, encoding="utf-8")
    print(f"\n✅ 完了: {output_path}")
    print(f"   フォルダ: {stats['folders']} 件 / ファイル: {stats['files']} 件")


if __name__ == "__main__":
    main()
