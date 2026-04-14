"""
P&L面談 Todo 自動作成スクリプト

【概要】
NotionのパイプラインDBで「選考状況=P&L面談」かつ「P&L面談」日時プロパティに
時刻付きの値が入っているレコードを検索し、ToDo DBに以下を自動作成する:
  - 面談30分前: タイトル「面談準備」
  - 面談30分後: タイトル「面談後処理」

同一パイプラインに同タイトルのTodoが既存の場合はスキップ（重複防止）。

作成されるTodoのプロパティ:
  - ステータス: 未着手
  - TaskType: NextAction 🚀
  - Category: Pole&Line
  - 優先度: 高
  - 開始時刻: 面談±30分
  - パイプライン: リレーション設定済み

【実行オプション】
  python scripts/meeting_todo_creator.py           # 通常実行
  python scripts/meeting_todo_creator.py --dry-run  # 確認のみ（更新なし）
  python scripts/meeting_todo_creator.py --debug    # 詳細ログ

【cron 例】毎朝9時
  0 9 * * * cd /path/to/pnl && python scripts/meeting_todo_creator.py >> logs/meeting_todo.log 2>&1
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

# ─── 設定 ────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_TOKEN  = os.environ["NOTION_TOKEN"]
PIPELINE_DB_ID = os.environ.get("PIPELINE_DB_ID", "20f7d017b6a0807ca60f000b827c6841")
TODO_DB_ID     = os.environ.get("TODO_DB_ID",     "2257d017b6a08026867c000bb0969507")

JST = ZoneInfo("Asia/Tokyo")

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

DEBUG   = "--debug"   in sys.argv
DRY_RUN = "--dry-run" in sys.argv


# ─── Notion API ────────────────────────────────────────────────────────────────

def query_pipeline_db() -> list[dict]:
    """
    パイプラインDBから「選考状況=P&L面談」かつ「P&L面談」日時プロパティに
    時刻付き（start に時刻が含まれる）値があるレコードを全件取得する。
    """
    results, has_more, cursor = [], True, None

    with httpx.Client(timeout=30) as client:
        while has_more:
            payload: dict = {
                "filter": {
                    "and": [
                        {
                            "property": "選考状況",
                            "status": {"equals": "P&L面談"},
                        },
                        {
                            "property": "P&L面談",
                            "date": {"is_not_empty": True},
                        },
                    ]
                },
                "page_size": 100,
            }
            if cursor:
                payload["start_cursor"] = cursor

            res = client.post(
                f"{NOTION_API}/databases/{PIPELINE_DB_ID}/query",
                headers=NOTION_HEADERS,
                json=payload,
            )
            res.raise_for_status()
            data = res.json()
            results.extend(data["results"])
            has_more = data.get("has_more", False)
            cursor   = data.get("next_cursor")

    return results


def get_existing_todos(pipeline_page_id: str) -> list[dict]:
    """
    指定パイプラインに紐づく ToDo レコードを全件取得する（重複チェック用）。
    """
    results, has_more, cursor = [], True, None

    with httpx.Client(timeout=30) as client:
        while has_more:
            payload: dict = {
                "filter": {
                    "property": "パイプライン",
                    "relation": {"contains": pipeline_page_id},
                },
                "page_size": 100,
            }
            if cursor:
                payload["start_cursor"] = cursor

            res = client.post(
                f"{NOTION_API}/databases/{TODO_DB_ID}/query",
                headers=NOTION_HEADERS,
                json=payload,
            )
            res.raise_for_status()
            data = res.json()
            results.extend(data["results"])
            has_more = data.get("has_more", False)
            cursor   = data.get("next_cursor")

    return results


def create_todo(
    title: str,
    start_dt: datetime,
    pipeline_page_id: str,
) -> dict:
    """
    ToDo DBに新しいレコードを作成する。
    """
    # Notion API は ISO 8601 形式（タイムゾーン付き）を受け付ける
    start_iso = start_dt.isoformat()

    payload = {
        "parent": {"database_id": TODO_DB_ID},
        "properties": {
            "Title": {
                "title": [{"text": {"content": title}}]
            },
            "ステータス": {
                "status": {"name": "未着手"}
            },
            "TaskType": {
                "select": {"name": "NextAction 🚀"}
            },
            "Category": {
                "select": {"name": "Pole&Line"}
            },
            "優先度": {
                "select": {"name": "高"}
            },
            "開始時刻": {
                "date": {"start": start_iso}
            },
            "パイプライン": {
                "relation": [{"id": pipeline_page_id}]
            },
        },
    }

    with httpx.Client(timeout=30) as client:
        res = client.post(
            f"{NOTION_API}/pages",
            headers=NOTION_HEADERS,
            json=payload,
        )
        res.raise_for_status()
        return res.json()


# ─── ヘルパー ───────────────────────────────────────────────────────────────────

def extract_pipeline_name(page: dict) -> str:
    """パイプラインページからタイトル（名前）を取得する"""
    title_parts = page["properties"].get("名前", {}).get("title", [])
    return "".join(t.get("plain_text", "") for t in title_parts) or page["id"]


def parse_meeting_datetime(page: dict) -> datetime | None:
    """
    「P&L面談」プロパティから datetime を取得する。
    時刻なし（日付のみ）の場合は None を返す。
    """
    date_prop = page["properties"].get("P&L面談", {}).get("date")
    if not date_prop:
        return None

    start_str = date_prop.get("start")
    if not start_str:
        return None

    # 時刻情報が含まれているか確認（Tが含まれる場合のみ）
    if "T" not in start_str:
        if DEBUG:
            print(f"  [DEBUG] 日付のみ（時刻なし）のためスキップ: {start_str}")
        return None

    # ISO 8601 形式をパース
    try:
        dt = datetime.fromisoformat(start_str)
        # タイムゾーンがない場合はJSTとみなす
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt
    except ValueError as e:
        if DEBUG:
            print(f"  [DEBUG] 日時パース失敗: {start_str!r} → {e}")
        return None


def get_existing_todo_titles(pipeline_page_id: str) -> set[str]:
    """既存ToDoのタイトル一覧をセットで返す（重複チェック用）"""
    todos = get_existing_todos(pipeline_page_id)
    titles = set()
    for todo in todos:
        title_parts = todo["properties"].get("Title", {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_parts)
        if title:
            titles.add(title)
    return titles


# ─── メイン処理 ───────────────────────────────────────────────────────────────

def main() -> None:
    ts = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] P&L面談 Todo 自動作成スクリプト開始")

    if DRY_RUN:
        print("⚠️  --dry-run モード: Notion の更新は行いません")

    # ① パイプラインDB から P&L面談のレコードを取得
    print("\n📋 パイプラインDBから「P&L面談」レコードを取得中...")
    try:
        pipeline_pages = query_pipeline_db()
    except httpx.HTTPStatusError as e:
        print(f"❌ パイプラインDB取得失敗: {e.response.status_code} {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ パイプラインDB取得失敗: {e}")
        sys.exit(1)

    print(f"   {len(pipeline_pages)} 件取得（選考状況=P&L面談 かつ 面談日時あり）")

    if not pipeline_pages:
        print("ℹ️  対象レコードが見つかりませんでした。")
        return

    # ② 各レコードに対して面談前後のToDoを作成
    created_count = 0
    skipped_count = 0

    for page in pipeline_pages:
        pipeline_id   = page["id"]
        pipeline_name = extract_pipeline_name(page)
        meeting_dt    = parse_meeting_datetime(page)

        if meeting_dt is None:
            if DEBUG:
                print(f"\n  [DEBUG] {pipeline_name!r}: 時刻なし → スキップ")
            continue

        meeting_dt_jst = meeting_dt.astimezone(JST)

        # 現在時刻より過去の面談はスキップ
        now = datetime.now(tz=JST)
        if meeting_dt_jst < now:
            print(f"\n  パイプライン: {pipeline_name!r}")
            print(f"  面談日時: {meeting_dt_jst.strftime('%Y-%m-%d %H:%M')} JST → 過去のためスキップ")
            continue

        print(f"\n  パイプライン: {pipeline_name!r}")
        print(f"  面談日時: {meeting_dt_jst.strftime('%Y-%m-%d %H:%M')} JST")

        # 既存ToDo取得（重複チェック）
        try:
            existing_titles = get_existing_todo_titles(pipeline_id)
        except Exception as e:
            print(f"  ⚠️  既存ToDo取得失敗: {e} → スキップ")
            continue

        if DEBUG:
            print(f"  [DEBUG] 既存ToDoタイトル: {existing_titles}")

        # 面談30分前: 「面談準備」
        before_title = "面談準備"
        before_dt    = meeting_dt - timedelta(minutes=30)

        # 面談30分後: 「面談後処理」
        after_title = "面談後処理"
        after_dt    = meeting_dt + timedelta(minutes=30)

        for title, start_dt in [(before_title, before_dt), (after_title, after_dt)]:
            start_dt_jst = start_dt.astimezone(JST)

            if title in existing_titles:
                print(f"  ⏭️  スキップ（既存）: 「{title}」 @ {start_dt_jst.strftime('%H:%M')}")
                skipped_count += 1
                continue

            if DRY_RUN:
                print(f"  [dry-run] 作成予定: 「{title}」 @ {start_dt_jst.strftime('%Y-%m-%d %H:%M')} JST")
                created_count += 1
            else:
                try:
                    create_todo(
                        title=title,
                        start_dt=start_dt,
                        pipeline_page_id=pipeline_id,
                    )
                    print(f"  ✅ 作成: 「{title}」 @ {start_dt_jst.strftime('%Y-%m-%d %H:%M')} JST")
                    created_count += 1
                except httpx.HTTPStatusError as e:
                    print(f"  ❌ 作成失敗 ({title}): {e.response.status_code} {e.response.text}")
                except Exception as e:
                    print(f"  ❌ 作成失敗 ({title}): {e}")

    # ③ 結果サマリー
    ts_end = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    action = "作成予定" if DRY_RUN else "作成"
    print(f"\n[{ts_end}] 完了: {action}={created_count} 件, スキップ={skipped_count} 件")


if __name__ == "__main__":
    main()
