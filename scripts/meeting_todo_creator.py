"""
パイプラインDB の「P&L面談」日時をもとに面談準備・後処理 ToDo を自動作成するスクリプト

【動作概要】
  パイプラインDB から:
    - 選考状況 = "P&L面談"
    - P&L面談日時 が入力済み（日付＋時刻）
  のレコードを取得し、ToDo DB に以下を作成する:
    - 面談日時 - 30分: 「面談準備」
    - 面談日時 + 30分: 「面談後処理」

【重複防止】
  同一パイプラインに対して既に同タイトルの ToDo が存在する場合はスキップする。

【セットアップ】
  .env に以下を設定:
    NOTION_TOKEN=secret_xxxxx
    PIPELINE_DB_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    TODO_DB_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

【実行オプション】
  --dry-run   Notion を更新せず確認のみ
  --debug     詳細ログを表示

【cron 例】毎朝9時
  0 9 * * * cd /path/to/pnl && python scripts/meeting_todo_creator.py >> logs/meeting_todo.log 2>&1
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

# ─── 設定 ────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
PIPELINE_DB_ID = os.environ.get("PIPELINE_DB_ID", "20f7d017b6a0807ca60f000b827c6841")
TODO_DB_ID = os.environ.get("TODO_DB_ID", "2257d017b6a08026867c000bb0969507")

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

JST = ZoneInfo("Asia/Tokyo")
DRY_RUN = "--dry-run" in sys.argv
DEBUG = "--debug" in sys.argv


# ─── Notion: パイプライン取得 ────────────────────────────────────────────────

async def query_pipeline_pnl_meetings(client: httpx.AsyncClient) -> list[dict]:
    """
    パイプラインDB から「選考状況=P&L面談」かつ「P&L面談日時が入力済み」の
    レコードを全件取得する。
    """
    results, has_more, cursor = [], True, None

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

        res = await client.post(
            f"{NOTION_API}/databases/{PIPELINE_DB_ID}/query",
            headers=NOTION_HEADERS,
            json=payload,
        )
        res.raise_for_status()
        data = res.json()
        results.extend(data["results"])
        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")

    return results


# ─── Notion: ToDo 既存チェック ───────────────────────────────────────────────

async def get_existing_todo_titles(
    pipeline_page_id: str,
    client: httpx.AsyncClient,
) -> set[str]:
    """
    ToDo DB から指定パイプラインに紐づく Todo のタイトル一覧を返す。
    重複作成防止に使用する。
    """
    titles: set[str] = set()
    has_more, cursor = True, None

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

        res = await client.post(
            f"{NOTION_API}/databases/{TODO_DB_ID}/query",
            headers=NOTION_HEADERS,
            json=payload,
        )
        res.raise_for_status()
        data = res.json()

        for page in data["results"]:
            title_parts = page["properties"].get("Title", {}).get("title", [])
            title = "".join(t.get("plain_text", "") for t in title_parts)
            if title:
                titles.add(title)

        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")

    return titles


# ─── Notion: ToDo 作成 ───────────────────────────────────────────────────────

async def create_todo(
    title: str,
    start_time: datetime,
    pipeline_page_id: str,
    client: httpx.AsyncClient,
) -> dict:
    """
    ToDo DB に新しいタスクを作成する。
    パイプラインリレーションを設定し、開始時刻に面談の前後30分を指定する。
    """
    start_str = start_time.astimezone(JST).isoformat()

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
                "date": {"start": start_str}
            },
            "パイプライン": {
                "relation": [{"id": pipeline_page_id}]
            },
        },
    }

    res = await client.post(
        f"{NOTION_API}/pages",
        headers=NOTION_HEADERS,
        json=payload,
    )
    res.raise_for_status()
    return res.json()


# ─── 日時パース ──────────────────────────────────────────────────────────────

def parse_notion_datetime(start_str: str) -> datetime | None:
    """
    Notion の date プロパティ start 文字列を datetime に変換する。
    日付のみ（時刻なし）の場合は None を返す。

    対応フォーマット:
      "2026-04-14T10:00:00.000+09:00"  → datetime（JST）
      "2026-04-14T01:00:00.000Z"       → datetime（UTC→JST）
      "2026-04-14T10:00:00+09:00"      → datetime（JST）
      "2026-04-14"                     → None（時刻なし）
    """
    if "T" not in start_str:
        return None  # 日付のみ（時刻情報なし）

    # "Z" を UTC オフセットに変換してパース
    normalized = start_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


# ─── メイン処理 ───────────────────────────────────────────────────────────────

async def main() -> None:
    ts = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] P&L面談 ToDo 自動作成 開始")

    if DRY_RUN:
        print("⚠️  --dry-run モード: Notion の更新は行いません")

    async with httpx.AsyncClient(timeout=30) as client:
        # ① パイプラインを取得
        print("\n🔍 パイプラインDB から P&L面談エントリを取得中...")
        try:
            pipelines = await query_pipeline_pnl_meetings(client)
        except httpx.HTTPStatusError as e:
            print(f"❌ パイプラインDB 取得失敗: {e.response.status_code} {e.response.text}")
            sys.exit(1)

        print(f"   {len(pipelines)} 件取得")

        if not pipelines:
            print("ℹ️  対象のパイプラインが見つかりませんでした。")
            return

        created_count = 0
        skipped_count = 0

        # ② 各パイプラインに対して Todo を作成
        for pipeline in pipelines:
            page_id = pipeline["id"]

            # パイプライン名
            title_parts = pipeline["properties"].get("名前", {}).get("title", [])
            pipeline_name = "".join(t.get("plain_text", "") for t in title_parts) or page_id

            # P&L面談の日時
            meeting_date_prop = pipeline["properties"].get("P&L面談", {}).get("date")
            if not meeting_date_prop or not meeting_date_prop.get("start"):
                if DEBUG:
                    print(f"  [DEBUG] スキップ（日時なし）: {pipeline_name!r}")
                continue

            start_str = meeting_date_prop["start"]
            meeting_dt = parse_notion_datetime(start_str)

            if meeting_dt is None:
                print(f"  ⏭️  スキップ（時刻なし・日付のみ）: {pipeline_name!r}  ({start_str})")
                skipped_count += 1
                continue

            prep_time = meeting_dt - timedelta(minutes=30)
            followup_time = meeting_dt + timedelta(minutes=30)

            if DEBUG:
                print(f"\n  [DEBUG] {pipeline_name!r}")
                print(f"         面談:    {meeting_dt.astimezone(JST).strftime('%Y-%m-%d %H:%M')} JST")
                print(f"         面談準備: {prep_time.astimezone(JST).strftime('%Y-%m-%d %H:%M')} JST")
                print(f"         面談後処理: {followup_time.astimezone(JST).strftime('%Y-%m-%d %H:%M')} JST")

            # ③ 既存 Todo を確認（重複防止）
            try:
                existing_titles = await get_existing_todo_titles(page_id, client)
            except httpx.HTTPStatusError as e:
                print(f"  ⚠️  既存Todo確認失敗 ({pipeline_name!r}): {e.response.status_code}")
                continue

            if DEBUG and existing_titles:
                print(f"  [DEBUG] 既存Todo: {existing_titles}")

            todos_to_create = [
                ("面談準備", prep_time),
                ("面談後処理", followup_time),
            ]

            all_skipped = True
            for todo_title, todo_time in todos_to_create:
                if todo_title in existing_titles:
                    if DEBUG:
                        print(f"  [DEBUG] スキップ（既存）: {todo_title!r} → {pipeline_name!r}")
                    continue

                all_skipped = False
                time_str = todo_time.astimezone(JST).strftime("%Y-%m-%d %H:%M")

                if DRY_RUN:
                    print(f"  [dry-run] 作成予定: 「{todo_title}」@ {time_str} JST → {pipeline_name!r}")
                else:
                    try:
                        await create_todo(todo_title, todo_time, page_id, client)
                        print(f"  ✅ 作成: 「{todo_title}」@ {time_str} JST → {pipeline_name!r}")
                        created_count += 1
                    except httpx.HTTPStatusError as e:
                        print(f"  ❌ 作成失敗 ({todo_title!r}): {e.response.status_code} {e.response.text}")

            if all_skipped:
                print(f"  ✅ スキップ（既存）: {pipeline_name!r}")
                skipped_count += 1

    ts_end = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts_end}] 完了: {created_count} 件作成, {skipped_count} 件スキップ")


if __name__ == "__main__":
    asyncio.run(main())
