"""
LinkedIn Recruiter 送信数 → Notion スカウトDB 加算同期スクリプト

【概要】
LinkedIn Recruiter の送信済み InMail から「今日送信した件数」をプロジェクト別に集計し、
Notion スカウトDB の「送信数」フィールドに加算する。

【マッチング方式】
  LinkedIn プロジェクト名: "セキュリティ統括責任者（CISO候補）-GFT"
       ↓ ハイフン+コードを除去してポジション名を抽出
  "セキュリティ統括責任者（CISO候補）"
       ↓ Notion ポジションDB.名前 と照合（NFKC正規化後に一致比較）
  ポジションに紐づく スカウトDB (DB=Linkedin かつ 使用中=true) の送信数を加算

【前提】
  - LinkedIn Recruiter プロジェクト名が "{ポジション名}-{コード}" 形式であること
    例: "事業企画担当-GFT"、"セキュリティ統括責任者（CISO候補）-GFT"
  - Notion ポジションDB.名前 が LinkedIn のポジション名と一致していること

【セットアップ】
  pip install -r requirements.txt
  playwright install chromium
  cp .env.example .env          # NOTION_TOKEN を記入
  python scripts/linkedin_notion_sync.py --save-session

【実行オプション】
  --save-session  初回ログイン（ブラウザが開く）
  --debug         ブラウザ表示あり＋詳細ログ、HTMLをファイル保存
  --dry-run       Notion を更新せず確認のみ
  --force         同日2回目も強制実行

【cron 例】毎日22時
  0 22 * * * cd /path/to/pnl && python scripts/linkedin_notion_sync.py >> logs/sync.log 2>&1
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
# スカウトDB の Notion database ID
# 「https://www.notion.so/{この部分}?v=...」の値（ダッシュなし32文字）
SCOUT_DB_ID = os.environ.get("SCOUT_DB_ID", "2597d017b6a0808ea499c4ec941d2a96")

SESSION_FILE = Path(__file__).parent / "linkedin_session.json"
STATE_FILE   = Path(__file__).parent / "sync_state.json"

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


# ─── LinkedIn: セッション保存（初回のみ） ────────────────────────────────────

async def save_session() -> None:
    """ブラウザを起動して手動ログイン → セッションを保存"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=500)
        context = await browser.new_context()
        page = await context.new_page()

        print("=== LinkedIn にログインしてください ===")
        print("LinkedIn Recruiter のトップページ（/talent/）まで進むと自動保存されます。")
        await page.goto("https://www.linkedin.com/login")
        await page.wait_for_url("**/talent/**", timeout=300_000)

        storage = await context.storage_state()
        SESSION_FILE.write_text(json.dumps(storage, ensure_ascii=False, indent=2))
        print(f"✅ セッション保存: {SESSION_FILE}")
        await browser.close()


# ─── LinkedIn: 送信数スクレイピング ──────────────────────────────────────────

def parse_linkedin_project(project_name: str) -> tuple[str, str] | None:
    """
    LinkedIn プロジェクト名からポジション名とコードを分解する。

    対応フォーマット:
      "事業企画担当-GFT"                        → ("事業企画担当", "GFT")
      "セキュリティ統括責任者（CISO候補）-GFT"  → ("セキュリティ統括責任者（CISO候補）", "GFT")
      "社長候補-BST"                             → ("社長候補", "BST")

    区切りは末尾の「-{大文字2〜4文字}」を採用。
    """
    m = re.match(r"^(.+?)-([A-Z]{2,4})$", project_name.strip())
    if m:
        return m.group(1).strip(), m.group(2)
    return None


async def get_today_sent_by_project(page: Page) -> dict[str, int]:
    """
    LinkedIn Recruiter の InMail 送信済み一覧から
    「今日送信した件数」をプロジェクト名別に集計する。

    戻り値: {"セキュリティ統括責任者（CISO候補）-GFT": 12, "事業企画担当-GFT": 7, ...}

    ─ セレクター調整について ─
    LinkedIn Recruiter の UI は変更されることがあります。
    うまく動かない場合は --debug で実行し、
    生成される debug_inbox.html を確認してセレクターを修正してください。
    """
    today_str = date.today(tz=JST).strftime("%-m/%-d")   # 例: "3/27"
    today_iso = date.today(tz=JST).isoformat()            # 例: "2026-03-27"
    sent_counts: dict[str, int] = {}

    # ① Sent InMail 画面へ移動
    await page.goto(
        "https://www.linkedin.com/talent/inbox?mailboxType=INMAIL",
        wait_until="domcontentloaded",
    )
    await page.wait_for_load_state("networkidle", timeout=20_000)

    # ② 「Sent」タブへ切り替え
    try:
        sent_tab = page.get_by_role("tab", name=re.compile(r"sent|送信済み", re.IGNORECASE))
        if await sent_tab.count() > 0:
            await sent_tab.first.click()
            await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception as e:
        if DEBUG:
            print(f"  [DEBUG] Sent タブ切り替え失敗（継続）: {e}")

    if DEBUG:
        html = await page.content()
        (Path(__file__).parent / "debug_inbox.html").write_text(html)
        print("  [DEBUG] debug_inbox.html を保存しました")

    # ③ メッセージスレッド一覧を走査
    #    各スレッドに「プロジェクト名（ジョブタイトル）」と「日付」が表示されている前提
    page_num = 0
    stop = False

    while not stop:
        page_num += 1

        # スレッドのリストアイテムを取得
        # ※ LinkedIn の UI 変更でセレクターが変わった場合はここを修正
        rows = await page.query_selector_all(
            "li.msg-conversation-listitem, "
            "[data-test-id='msg-conversation'], "
            ".scaffold-finite-scroll__content > ul > li"
        )

        if not rows:
            print(f"  ⚠️  スレッド要素が見つかりません（ページ {page_num}）")
            if DEBUG:
                print("     --debug モードでは debug_inbox.html を確認してください")
            break

        for row in rows:
            text = await row.inner_text()
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

            # 今日の日付が含まれているか判定
            is_today = any(today_str in ln or today_iso in ln for ln in lines)

            # 今日より前の日付（例: "3/20", "2026/03/20"）が含まれていれば打ち切り
            # ※ 一覧は新着順のため
            date_lines = [ln for ln in lines if re.search(r"\d{1,2}/\d{1,2}", ln)]
            is_older = any(
                today_str not in ln and re.search(r"\d{1,2}/\d{1,2}", ln)
                for ln in date_lines
            )
            if is_older and not is_today:
                stop = True
                break

            if not is_today:
                continue

            # プロジェクト名を抽出
            # LinkedIn Recruiter では各スレッドに関連ジョブタイトルが表示される
            # 「{ポジション名}-{コード}」形式の行を探す
            project_name = None
            for ln in lines:
                if parse_linkedin_project(ln):
                    project_name = ln
                    break

            if project_name:
                sent_counts[project_name] = sent_counts.get(project_name, 0) + 1
                if DEBUG:
                    print(f"  [DEBUG] カウント: {project_name!r}")
            else:
                if DEBUG:
                    print(f"  [DEBUG] プロジェクト名未検出（行テキスト）: {lines}")

        if stop:
            break

        # 次ページ
        next_btn = page.get_by_role(
            "button", name=re.compile(r"next|次へ", re.IGNORECASE)
        )
        if await next_btn.count() > 0 and await next_btn.first.is_enabled():
            await next_btn.first.click()
            await page.wait_for_load_state("networkidle", timeout=10_000)
        else:
            break

    return sent_counts


async def scrape_today_sent_counts() -> dict[str, int]:
    """セッションを読み込んで LinkedIn Recruiter をスクレイピング"""
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

        await page.goto("https://www.linkedin.com/talent/", wait_until="domcontentloaded")
        if "login" in page.url or "authwall" in page.url:
            raise RuntimeError(
                "セッションが切れています。--save-session で再ログインしてください。"
            )

        counts = await get_today_sent_by_project(page)

        await context.storage_state(path=str(SESSION_FILE))  # セッション更新
        await browser.close()

    return counts


# ─── Notion: スカウトDB + ポジション名の取得 ──────────────────────────────────

async def query_scout_db_linkedin() -> list[dict]:
    """
    スカウトDB から「DB=Linkedin かつ 使用中=true」のレコードを全件取得。
    ポジション リレーション（relation プロパティ）も含まれる。
    """
    results, has_more, cursor = [], True, None

    async with httpx.AsyncClient(timeout=30) as client:
        while has_more:
            payload: dict = {
                "filter": {
                    "and": [
                        {"property": "DB",   "multi_select": {"contains": "Linkedin"}},
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
    スカウトDB (Linkedin, 使用中) の各レコードに紐づく
    ポジション名を取得してリストで返す。

    戻り値:
      [
        {
          "scout_page_id": "xxx",
          "current_count": 15,
          "position_name": "セキュリティ統括責任者（CISO候補）",  # None の場合あり
          "scout_title": "GFT-CISO-v1",  # ログ用
        },
        ...
      ]
    """
    scout_pages = await query_scout_db_linkedin()
    if not scout_pages:
        return []

    # ポジション relation から page ID を収集（重複排除）
    pos_id_map: dict[str, str] = {}   # scout_page_id → position_page_id
    unique_pos_ids: set[str] = set()

    for page in scout_pages:
        rel = page["properties"].get("ポジション", {}).get("relation", [])
        if rel:
            pos_page_id = rel[0]["id"]
            pos_id_map[page["id"]] = pos_page_id
            unique_pos_ids.add(pos_page_id)

    # ポジション名を並列取得
    pos_name_cache: dict[str, str | None] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        tasks = {
            pos_id: asyncio.create_task(fetch_position_name(pos_id, client))
            for pos_id in unique_pos_ids
        }
        for pos_id, task in tasks.items():
            pos_name_cache[pos_id] = await task

    # スカウトエントリを組み立て
    entries = []
    for page in scout_pages:
        # 識別ID（title）
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


# ─── マッチング ────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """NFKC正規化（全角→半角等）＋前後スペース除去"""
    return unicodedata.normalize("NFKC", text).strip()


def find_matching_entry(
    linkedin_project: str,
    entries: list[dict],
) -> dict | None:
    """
    LinkedIn プロジェクト名に対応する Notion スカウトエントリを返す。

    ① ポジション名の完全一致（NFKC正規化後）
    ② 部分一致（どちらかが一方を含む）
    """
    parsed = parse_linkedin_project(linkedin_project)
    if not parsed:
        return None

    li_pos, _ = parsed
    li_norm = normalize(li_pos)

    # ① 完全一致
    for entry in entries:
        if entry["position_name"] and normalize(entry["position_name"]) == li_norm:
            return entry

    # ② 部分一致（どちらかがもう一方を含む）
    for entry in entries:
        if entry["position_name"]:
            n_norm = normalize(entry["position_name"])
            if li_norm in n_norm or n_norm in li_norm:
                return entry

    return None


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

async def main() -> None:
    today = date.today(tz=JST).isoformat()
    ts    = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] LinkedIn → Notion 送信数同期開始（対象日: {today}）")

    if DRY_RUN:
        print("⚠️  --dry-run モード: Notion の更新は行いません")

    # 二重実行チェック
    if already_synced_today() and not FORCE:
        print(f"✅ 本日 ({today}) は既に同期済みです。スキップします。（--force で強制実行）")
        return

    # ① LinkedIn から今日の送信数を取得
    print("\n📥 LinkedIn Recruiter から今日の送信数を取得中...")
    try:
        today_counts = await scrape_today_sent_counts()
    except Exception as e:
        print(f"❌ LinkedIn スクレイピング失敗: {e}")
        sys.exit(1)

    if not today_counts:
        print("ℹ️  今日の送信データが見つかりませんでした。")
        save_state(today, {})
        return

    print(f"📊 LinkedIn から取得（プロジェクト: 今日の件数）:")
    for proj, cnt in sorted(today_counts.items()):
        parsed = parse_linkedin_project(proj)
        pos_label = f"  ポジション名={parsed[0]!r}, コード={parsed[1]}" if parsed else "  ⚠️ 解析不可"
        print(f"   {proj!r}: {cnt} 件{pos_label}")

    # ② Notion スカウトDB（+ポジション名）を取得
    print("\n📋 Notion スカウトDB（LinkedIn, 使用中）を取得中...")
    try:
        scout_entries = await build_scout_entries()
    except Exception as e:
        print(f"❌ Notion DB 取得失敗: {e}")
        sys.exit(1)

    print(f"   {len(scout_entries)} 件取得:")
    for e in scout_entries:
        print(f"   識別ID={e['scout_title']!r}, ポジション名={e['position_name']!r}, 現在={e['current_count']}")

    # ③ マッチング → 加算更新
    print("\n🔄 マッチングして更新中...")
    added: dict[str, int] = {}
    unmatched_projects: list[str] = []

    for project_name, today_count in today_counts.items():
        entry = find_matching_entry(project_name, scout_entries)

        if not entry:
            unmatched_projects.append(project_name)
            continue

        new_total = entry["current_count"] + today_count
        label = f"識別ID={entry['scout_title']!r}, ポジション={entry['position_name']!r}"

        if DRY_RUN:
            print(f"   [dry-run] {label}")
            print(f"            {entry['current_count']} + {today_count} = {new_total}")
        else:
            await update_sent_count(entry["scout_page_id"], new_total)
            print(f"   ✅ {label}")
            print(f"      {entry['current_count']} + {today_count} → {new_total}")
            added[project_name] = today_count

    if unmatched_projects:
        print(f"\n⚠️  マッチしなかった LinkedIn プロジェクト:")
        for proj in unmatched_projects:
            parsed = parse_linkedin_project(proj)
            hint = f"  → ポジション名 {parsed[0]!r} が Notion に存在するか確認" if parsed else "  → プロジェクト名の形式を確認"
            print(f"   {proj!r}{hint}")

    # ④ 状態保存
    if not DRY_RUN:
        save_state(today, added)

    ts_end = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts_end}] 完了: {len(added)} プロジェクト更新")


if __name__ == "__main__":
    if "--save-session" in sys.argv:
        asyncio.run(save_session())
    else:
        asyncio.run(main())
