"""
YOUTRUST 送信数 → Notion スカウトDB 加算同期スクリプト

【概要】
YOUTRUST の送信済みスカウトから「今日送信した件数」をポジション別に集計し、
Notion スカウトDB の「送信数」フィールドに加算する。

【マッチング方式】
  YOUTRUST メッセージ送信先プロフィール → ポジション名タグ or 手動マッピング
  ※ YOUTRUST はプロジェクト別管理ではなくメッセージ一覧から集計する

【前提】
  - Notion スカウトDB に DB=YOUTRUST かつ 使用中=true のレコードがあること
  - YOUTRUST のスカウトメッセージ一覧ページが利用可能なこと

【セットアップ】
  pip install -r requirements.txt
  playwright install chromium
  cp .env.example .env          # NOTION_TOKEN を記入
  python scripts/youtrust_notion_sync.py --save-session

【実行オプション】
  --save-session  初回ログイン（ブラウザが開く）
  --debug         ブラウザ表示あり＋詳細ログ、HTMLをファイル保存
  --dry-run       Notion を更新せず確認のみ
  --force         同日2回目も強制実行
  --count N       今日の送信数を手動指定（スクレイピング不要な場合）
  --position NAME ポジション名を手動指定（--count と併用）
"""

import asyncio
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from playwright.async_api import Page, async_playwright

# ─── 設定 ────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
SCOUT_DB_ID = os.environ.get("SCOUT_DB_ID", "2597d017b6a0808ea499c4ec941d2a96")

SESSION_FILE = Path(__file__).parent / "youtrust_session.json"
STATE_FILE   = Path(__file__).parent / "youtrust_sync_state.json"

JST = ZoneInfo("Asia/Tokyo")

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

DEBUG   = "--debug"   in sys.argv
FORCE   = "--force"   in sys.argv
DRY_RUN = "--dry-run" in sys.argv

YOUTRUST_LOGIN_URL = "https://youtrust.jp/login"
YOUTRUST_SCOUT_URL = "https://youtrust.jp/scout_messages"


# ─── 二重実行防止 ─────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(today: str, added: dict[str, int]) -> None:
    STATE_FILE.write_text(
        json.dumps({"last_sync_date": today, "added_counts": added},
                   ensure_ascii=False, indent=2)
    )

def already_synced_today() -> bool:
    return load_state().get("last_sync_date") == date.today(tz=JST).isoformat()


# ─── YOUTRUST: セッション保存（初回のみ） ────────────────────────────────────

async def save_session() -> None:
    """ブラウザを起動して手動ログイン → セッションを保存"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=500)
        context = await browser.new_context()
        page = await context.new_page()

        print("=== YOUTRUST にログインしてください ===")
        print("ログイン完了後、スカウトメッセージ一覧（/scout_messages）まで進むと自動保存されます。")
        await page.goto(YOUTRUST_LOGIN_URL)
        await page.wait_for_url("**/scout_messages**", timeout=300_000)

        storage = await context.storage_state()
        SESSION_FILE.write_text(json.dumps(storage, ensure_ascii=False, indent=2))
        print(f"✅ セッション保存: {SESSION_FILE}")
        await browser.close()


# ─── YOUTRUST: 送信数スクレイピング ──────────────────────────────────────────

def parse_date_ja(text: str) -> str | None:
    """
    日本語日付テキストから "M月D日" 形式を抽出する。
    例: "2026年3月27日" → "3月27日"
         "3月27日 12:34" → "3月27日"
         "今日" → None（別途処理）
    """
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m:
        return f"{m.group(1)}月{m.group(2)}日"
    return None


async def count_today_scouts(page: Page) -> int:
    """
    YOUTRUST スカウトメッセージ送信一覧で「今日送信した件数」を返す。

    画面構造の想定:
      /scout_messages に送信済みスカウト一覧がある
      各メッセージに送信日時が表示されている
    """
    today_ja = datetime.now(tz=JST).strftime("%-m月%-d日")  # 例: "3月27日"
    today_full = datetime.now(tz=JST).strftime("%Y年%-m月%-d日")  # 例: "2026年3月27日"
    count = 0

    await page.goto(YOUTRUST_SCOUT_URL, wait_until="domcontentloaded")
    await page.wait_for_load_state("networkidle", timeout=20_000)

    if DEBUG:
        html = await page.content()
        (Path(__file__).parent / "debug_youtrust_scout.html").write_text(html)
        print(f"  [DEBUG] debug_youtrust_scout.html を保存（今日: {today_ja}）")

    # スカウトメッセージのリスト要素を探す
    # YOUTRUST のDOM構造に合わせてセレクタを調整してください
    selectors = [
        "[data-testid='scout-message-item']",
        ".scout-message-item",
        ".message-list-item",
        "article",
        "li[class*='message']",
        "li[class*='scout']",
        "div[class*='message-item']",
    ]

    rows = []
    for selector in selectors:
        rows = await page.query_selector_all(selector)
        if rows:
            if DEBUG:
                print(f"  [DEBUG] セレクタ '{selector}' で {len(rows)} 件ヒット")
            break

    if not rows:
        if DEBUG:
            # フォールバック: ページ全体のテキストを確認
            text = await page.inner_text("body")
            print(f"  [DEBUG] ページテキスト（最初の2000文字）:\n{text[:2000]}")
        print("  ⚠️  スカウトメッセージ要素が見つかりません。--debug で debug_youtrust_scout.html を確認してください。")
        return 0

    stop = False
    for row in rows:
        text = await row.inner_text()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        if DEBUG:
            print(f"  [DEBUG] 行テキスト: {lines[:5]}")

        # 「今日」テキストの確認
        full_text = " ".join(lines)
        is_today = False

        if "今日" in full_text:
            is_today = True
        else:
            date_str = parse_date_ja(full_text)
            if date_str and (today_ja in date_str or today_full in full_text):
                is_today = True
            elif date_str:
                # 今日より古い日付が出たら打ち切り
                stop = True
                break

        if is_today:
            count += 1
            if DEBUG:
                preview = lines[0] if lines else ""
                print(f"  [DEBUG]   ✓ 今日のスカウト: {preview!r}")

    if stop and DEBUG:
        print(f"  [DEBUG] 今日より古い日付で打ち切り")

    # ページネーションがある場合は次ページも確認
    while not stop:
        next_btn = page.get_by_role(
            "button", name=re.compile(r"next|次へ|次のページ", re.IGNORECASE)
        )
        if await next_btn.count() > 0 and await next_btn.first.is_enabled():
            await next_btn.first.click()
            await page.wait_for_load_state("networkidle", timeout=10_000)

            rows = []
            for selector in selectors:
                rows = await page.query_selector_all(selector)
                if rows:
                    break

            for row in rows:
                text = await row.inner_text()
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                full_text = " ".join(lines)

                if "今日" in full_text:
                    count += 1
                else:
                    date_str = parse_date_ja(full_text)
                    if date_str and today_ja in date_str:
                        count += 1
                    elif date_str:
                        stop = True
                        break
        else:
            break

    return count


async def scrape_today_sent_counts() -> int:
    """セッションを読み込んで YOUTRUST をスクレイピング"""
    if not SESSION_FILE.exists():
        raise FileNotFoundError(
            f"セッションファイルが見つかりません: {SESSION_FILE}\n"
            "先に --save-session で初回ログインしてください。"
        )

    storage = json.loads(SESSION_FILE.read_text())

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not DEBUG,
            slow_mo=500 if DEBUG else 100,
        )
        context = await browser.new_context(storage_state=storage)
        page = await context.new_page()

        await page.goto("https://youtrust.jp/", wait_until="domcontentloaded")
        if "login" in page.url or "sign_in" in page.url:
            raise RuntimeError(
                "セッションが切れています。--save-session で再ログインしてください。"
            )

        count = await count_today_scouts(page)

        await context.storage_state(path=str(SESSION_FILE))  # セッション更新
        await browser.close()

    return count


# ─── Notion: スカウトDB（YOUTRUST）取得 ───────────────────────────────────────

async def query_scout_db_youtrust() -> list[dict]:
    """
    スカウトDB から「DB=YOUTRUST かつ 使用中=true」のレコードを全件取得。
    """
    results, has_more, cursor = [], True, None

    async with httpx.AsyncClient(timeout=30) as client:
        while has_more:
            payload: dict = {
                "filter": {
                    "and": [
                        {"property": "DB",   "multi_select": {"contains": "YOUTRUST"}},
                        {"property": "使用中", "checkbox": {"equals": True}},
                    ]
                },
                "page_size": 100,
            }
            if cursor:
                payload["start_cursor"] = cursor

            res = await client.post(
                f"{NOTION_API}/databases/{SCOUT_DB_ID}/query",
                headers=NOTION_HEADERS,
                json=payload,
            )
            res.raise_for_status()
            data = res.json()
            results.extend(data["results"])
            has_more = data.get("has_more", False)
            cursor = data.get("next_cursor")

    return results


async def fetch_position_name(position_page_id: str, client: httpx.AsyncClient) -> str | None:
    """ポジションページを取得して 名前（title）を返す"""
    try:
        res = await client.get(
            f"{NOTION_API}/pages/{position_page_id}",
            headers=NOTION_HEADERS,
        )
        res.raise_for_status()
        data = res.json()
        title_parts = data.get("properties", {}).get("名前", {}).get("title", [])
        return "".join(t.get("plain_text", "") for t in title_parts) or None
    except Exception as e:
        if DEBUG:
            print(f"  [DEBUG] ポジション取得失敗 ({position_page_id}): {e}")
        return None


async def build_scout_entries() -> list[dict]:
    """
    スカウトDB (YOUTRUST, 使用中) の各レコードに紐づく
    ポジション名を取得してリストで返す。
    """
    scout_pages = await query_scout_db_youtrust()
    if not scout_pages:
        return []

    pos_id_map: dict[str, str] = {}
    unique_pos_ids: set[str] = set()

    for page in scout_pages:
        rel = page["properties"].get("ポジション", {}).get("relation", [])
        if rel:
            pos_page_id = rel[0]["id"]
            pos_id_map[page["id"]] = pos_page_id
            unique_pos_ids.add(pos_page_id)

    pos_name_cache: dict[str, str | None] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        tasks = {
            pos_id: asyncio.create_task(fetch_position_name(pos_id, client))
            for pos_id in unique_pos_ids
        }
        for pos_id, task in tasks.items():
            pos_name_cache[pos_id] = await task

    entries = []
    for page in scout_pages:
        title_parts = page["properties"].get("識別ID", {}).get("title", [])
        scout_title = "".join(t.get("plain_text", "") for t in title_parts)

        pos_page_id = pos_id_map.get(page["id"])
        pos_name = pos_name_cache.get(pos_page_id) if pos_page_id else None

        entries.append({
            "scout_page_id": page["id"],
            "current_count": page["properties"].get("送信数", {}).get("number") or 0,
            "position_name": pos_name,
            "scout_title": scout_title,
        })

    return entries


# ─── Notion: 送信数更新 ───────────────────────────────────────────────────────

async def update_sent_count(page_id: str, new_total: int) -> None:
    """スカウトDBの送信数を更新"""
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers=NOTION_HEADERS,
            json={"properties": {"送信数": {"number": new_total}}},
        )
        res.raise_for_status()


# ─── メイン処理 ───────────────────────────────────────────────────────────────

def get_manual_count() -> tuple[int, str] | None:
    """--count N --position NAME オプションを解析"""
    args = sys.argv[1:]
    count = None
    position = None
    for i, arg in enumerate(args):
        if arg == "--count" and i + 1 < len(args):
            try:
                count = int(args[i + 1])
            except ValueError:
                pass
        if arg == "--position" and i + 1 < len(args):
            position = args[i + 1]
    if count is not None and position is not None:
        return count, position
    return None


def normalize(text: str) -> str:
    """NFKC正規化（全角→半角等）＋前後スペース除去"""
    return unicodedata.normalize("NFKC", text).strip()


async def main() -> None:
    today = date.today(tz=JST).isoformat()
    ts    = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] YOUTRUST → Notion 送信数同期開始（対象日: {today}）")

    if DRY_RUN:
        print("⚠️  --dry-run モード: Notion の更新は行いません")

    # 二重実行チェック
    if already_synced_today() and not FORCE:
        print(f"✅ 本日 ({today}) は既に同期済みです。スキップします。（--force で強制実行）")
        return

    # ① 今日の送信数を取得
    manual = get_manual_count()

    if manual:
        # 手動指定モード
        today_count, target_position = manual
        print(f"\n📥 手動指定: {target_position!r} に {today_count} 件")
    else:
        # スクレイピングモード
        print("\n📥 YOUTRUST から今日の送信数を取得中...")
        try:
            today_count = await scrape_today_sent_counts()
        except Exception as e:
            print(f"❌ YOUTRUST スクレイピング失敗: {e}")
            sys.exit(1)
        target_position = None

    if today_count == 0 and not manual:
        print("ℹ️  今日の送信データが見つかりませんでした。")
        save_state(today, {})
        return

    print(f"📊 今日の YOUTRUST 送信数: {today_count} 件")

    # ② Notion スカウトDB（YOUTRUST, 使用中）を取得
    print("\n📋 Notion スカウトDB（YOUTRUST, 使用中）を取得中...")
    try:
        scout_entries = await build_scout_entries()
    except Exception as e:
        print(f"❌ Notion DB 取得失敗: {e}")
        sys.exit(1)

    if not scout_entries:
        print("  ⚠️  YOUTRUST の使用中スカウトレコードが見つかりません。")
        return

    print(f"   {len(scout_entries)} 件取得:")
    for e in scout_entries:
        print(f"   識別ID={e['scout_title']!r}, ポジション名={e['position_name']!r}, 現在={e['current_count']}")

    # ③ 更新対象を決定
    print("\n🔄 更新中...")
    added: dict[str, int] = {}

    if target_position:
        # 手動指定: ポジション名でマッチング
        norm_target = normalize(target_position)
        matched = [
            e for e in scout_entries
            if e["position_name"] and normalize(e["position_name"]) == norm_target
        ]
        if not matched:
            # 部分一致
            matched = [
                e for e in scout_entries
                if e["position_name"] and (
                    norm_target in normalize(e["position_name"]) or
                    normalize(e["position_name"]) in norm_target
                )
            ]
        if not matched:
            print(f"  ⚠️  ポジション {target_position!r} に対応するスカウトレコードが見つかりません")
            return

        for entry in matched:
            new_total = entry["current_count"] + today_count
            label = f"識別ID={entry['scout_title']!r}, ポジション={entry['position_name']!r}"
            if DRY_RUN:
                print(f"   [dry-run] {label}")
                print(f"            {entry['current_count']} + {today_count} = {new_total}")
            else:
                await update_sent_count(entry["scout_page_id"], new_total)
                print(f"   ✅ {label}")
                print(f"      {entry['current_count']} + {today_count} → {new_total}")
                added[entry["scout_title"]] = today_count
    else:
        # スクレイピングモード: 使用中の全レコードに加算
        # YOUTRUST は LinkedIn のようなプロジェクト分け情報が取れないため、
        # 使用中レコードが1件の場合はそのまま加算、複数件の場合は警告
        if len(scout_entries) == 1:
            entry = scout_entries[0]
            new_total = entry["current_count"] + today_count
            label = f"識別ID={entry['scout_title']!r}, ポジション={entry['position_name']!r}"
            if DRY_RUN:
                print(f"   [dry-run] {label}")
                print(f"            {entry['current_count']} + {today_count} = {new_total}")
            else:
                await update_sent_count(entry["scout_page_id"], new_total)
                print(f"   ✅ {label}")
                print(f"      {entry['current_count']} + {today_count} → {new_total}")
                added[entry["scout_title"]] = today_count
        else:
            print(
                f"  ⚠️  YOUTRUST の使用中スカウトレコードが {len(scout_entries)} 件あります。\n"
                f"     どのポジションに加算するか指定してください:\n"
                f"     python scripts/youtrust_notion_sync.py --count {today_count} --position \"ポジション名\""
            )
            for e in scout_entries:
                print(f"     - {e['position_name']!r} (識別ID: {e['scout_title']!r})")
            return

    # ④ 状態保存
    if not DRY_RUN:
        save_state(today, added)

    ts_end = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts_end}] 完了: {len(added)} レコード更新")


if __name__ == "__main__":
    if "--save-session" in sys.argv:
        asyncio.run(save_session())
    else:
        asyncio.run(main())
