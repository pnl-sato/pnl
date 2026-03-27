"""
LinkedIn Recruiter 送信数 → Notion スカウトDB 加算同期スクリプト

【概要】
LinkedIn Recruiter のプロジェクト（ポジション）ごとに「今日送信したInMail数」を集計し、
Notion スカウトDB の「送信数」フィールドに加算する。
既存のデータは保持され、今日の分だけ上乗せされる。

【前提】
- LinkedIn Recruiter のプロジェクト名にポジションコードが含まれている
  例: "GFT - セキュリティ統括責任者候補"、"BST_事業企画担当" など
- Notion スカウトDB の 識別ID にポジションコードが含まれている
  例: "GFT-CISO-v1"、"BST-BizDev-v2" など
- 1日1回 cron で実行することを想定

【初回セットアップ】
  pip install -r requirements.txt
  playwright install chromium
  cp .env.example .env          # NOTION_TOKEN を記入
  python scripts/linkedin_notion_sync.py --save-session

【定期実行（cron）例】毎日22時に実行
  0 22 * * * cd /path/to/pnl && python scripts/linkedin_notion_sync.py >> logs/sync.log 2>&1

【手動実行・デバッグ】
  python scripts/linkedin_notion_sync.py --debug    # ブラウザ表示あり
  python scripts/linkedin_notion_sync.py --force    # 当日2回目でも強制実行
  python scripts/linkedin_notion_sync.py --dry-run  # Notion を更新せず確認のみ
"""

import asyncio
import json
import os
import re
import sys
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

SESSION_FILE = Path(__file__).parent / "linkedin_session.json"
STATE_FILE   = Path(__file__).parent / "sync_state.json"   # 二重実行防止用

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
        json.dumps({"last_sync_date": today, "added_counts": added}, ensure_ascii=False, indent=2)
    )

def already_synced_today() -> bool:
    state = load_state()
    return state.get("last_sync_date") == date.today(tz=JST).isoformat()


# ─── LinkedIn セッション保存（初回のみ） ─────────────────────────────────────

async def save_session() -> None:
    """ブラウザを起動して手動ログイン → セッションを保存する"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=500)
        context = await browser.new_context()
        page = await context.new_page()

        print("=== LinkedIn にログインしてください ===")
        print("LinkedIn Recruiter のトップページ（/talent/）まで進んだら自動保存されます。")
        await page.goto("https://www.linkedin.com/login")
        await page.wait_for_url("**/talent/**", timeout=300_000)

        storage = await context.storage_state()
        SESSION_FILE.write_text(json.dumps(storage, ensure_ascii=False, indent=2))
        print(f"✅ セッション保存: {SESSION_FILE}")
        await browser.close()


# ─── LinkedIn スクレイピング ───────────────────────────────────────────────────

def extract_code_from_project_name(name: str) -> str | None:
    """
    LinkedIn Recruiter プロジェクト名からポジションコードを抽出する。

    対応フォーマット例:
      "GFT - セキュリティ統括責任者候補"   → "GFT"
      "BST_事業企画担当"                   → "BST"
      "MDY/介護領域COO候補"               → "MDY"
      "【GFT】CISO候補"                   → "GFT"
      "#GTT サイバーセキュリティ"          → "GTT"

    ※ コードが先頭・区切り文字の直後にある大文字2〜4文字を抽出。
    　 マッチしない場合は None を返す（ログに出るので確認して正規表現を調整）。
    """
    patterns = [
        r"^([A-Z]{2,4})[\s\-_/【】#]",   # 先頭: GFT - / BST_ / MDY/ / 【GFT】/ #GTT
        r"^([A-Z]{2,4})$",               # コードのみ
        r"#([A-Z]{2,4})\b",              # 文中の #GFT
    ]
    for pat in patterns:
        m = re.search(pat, name.strip())
        if m:
            return m.group(1)
    return None


async def get_today_sent_counts(page: Page) -> dict[str, int]:
    """
    LinkedIn Recruiter の InMail 送信済み一覧から「今日送信した件数」を
    プロジェクトコード別に集計して返す。

    戻り値例: {"GFT": 12, "BST": 7, "MDY": 3}
    """
    today_str = date.today(tz=JST).strftime("%-m/%-d")   # 例: "3/27"
    today_iso = date.today(tz=JST).isoformat()            # 例: "2026-03-27"
    sent_counts: dict[str, int] = {}

    # ── ① LinkedIn Recruiter InMail の Sent 画面に移動 ──────────────────────
    # LinkedIn Recruiter のメッセージ画面は複数のURLパターンがある。
    # うまく開かない場合は --debug で確認し、URL を調整する。
    inbox_url = "https://www.linkedin.com/talent/inbox"
    await page.goto(inbox_url, wait_until="domcontentloaded")
    await page.wait_for_load_state("networkidle", timeout=20_000)

    # ── ② 「Sent」タブに切り替え ─────────────────────────────────────────────
    try:
        sent_tab = page.get_by_role("tab", name=re.compile("sent|送信済み", re.IGNORECASE))
        if await sent_tab.count() > 0:
            await sent_tab.first.click()
            await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception as e:
        print(f"  ⚠️  Sent タブへの切り替え失敗（継続）: {e}")

    # ── ③ メッセージリストを走査 ─────────────────────────────────────────────
    # LinkedIn の InMail 一覧には「日付」と「プロジェクト名（ジョブタイトル）」が表示される。
    # 今日の日付のメッセージのみカウントし、プロジェクト名からコードを抽出する。

    page_num = 0
    stop = False

    while not stop:
        page_num += 1
        if DEBUG:
            print(f"  [DEBUG] InMail Sent ページ {page_num} を解析中...")

        # セレクター候補（LinkedIn の UI 変更に合わせて要調整）
        # data-test-id や aria-label は LinkedIn が変更することがある
        rows = await page.query_selector_all(
            "li.msg-conversation-listitem, "
            "[data-test-id='msg-conversation'], "
            ".scaffold-finite-scroll__content li"
        )

        if not rows:
            # フォールバック: ページ全体の HTML を取得してデバッグ出力
            print(f"  ⚠️  メッセージ要素が見つかりません（ページ {page_num}）")
            if DEBUG:
                html = await page.content()
                debug_path = Path(__file__).parent / f"debug_page{page_num}.html"
                debug_path.write_text(html)
                print(f"  [DEBUG] HTML 保存: {debug_path}")
            break

        for row in rows:
            text = await row.inner_text()
            lines = [l.strip() for l in text.splitlines() if l.strip()]

            # 日付判定: 今日でなければそれ以降は不要（一覧は新着順のため）
            has_today = any(
                today_str in line or today_iso in line
                for line in lines
            )
            is_older = any(
                re.search(r"\d{1,2}/\d{1,2}", line) and today_str not in line
                for line in lines
            ) or any("/" in line and today_str not in line for line in lines if len(line) < 20)

            if is_older and not has_today:
                stop = True
                break

            if not has_today:
                continue

            # プロジェクト名（ジョブタイトル）からコード抽出
            # LinkedIn では「プロジェクト名」がメッセージリスト行のどこかに表示される
            # 実際の表示位置はページ構造による → 全行を試す
            for line in lines:
                code = extract_code_from_project_name(line)
                if code:
                    sent_counts[code] = sent_counts.get(code, 0) + 1
                    if DEBUG:
                        print(f"  [DEBUG] '{line}' → コード: {code}")
                    break
            else:
                if DEBUG:
                    print(f"  [DEBUG] コード抽出失敗（行テキスト）: {lines}")

        if stop:
            break

        # 次のページへ
        next_btn = page.get_by_role("button", name=re.compile("next|次へ", re.IGNORECASE))
        if await next_btn.count() > 0 and await next_btn.first.is_enabled():
            await next_btn.first.click()
            await page.wait_for_load_state("networkidle", timeout=10_000)
        else:
            break

    return sent_counts


async def scrape_today_sent_counts() -> dict[str, int]:
    """セッションを使って LinkedIn Recruiter から今日の送信数を取得する"""
    if not SESSION_FILE.exists():
        raise FileNotFoundError(
            f"セッションファイルが見つかりません: {SESSION_FILE}\n"
            "先に `python scripts/linkedin_notion_sync.py --save-session` を実行してください。"
        )

    storage = json.loads(SESSION_FILE.read_text())

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not DEBUG,
            slow_mo=500 if DEBUG else 100,
        )
        context = await browser.new_context(storage_state=storage)
        page = await context.new_page()

        # セッション確認
        await page.goto("https://www.linkedin.com/talent/", wait_until="domcontentloaded")
        if "login" in page.url or "authwall" in page.url:
            raise RuntimeError(
                "セッションが切れています。`--save-session` で再ログインしてください。"
            )

        counts = await get_today_sent_counts(page)

        # セッション更新（有効期限を延ばす）
        await context.storage_state(path=str(SESSION_FILE))
        await browser.close()

    return counts


# ─── Notion 操作 ─────────────────────────────────────────────────────────────

async def query_scout_db_linkedin() -> list[dict]:
    """
    スカウトDB から「DB=Linkedin かつ 使用中=true」のレコードを全件取得する。

    - DB=Linkedin : LinkedIn 以外（Bizreach・dodaX 等）のエントリは除外
    - 使用中=true : 旧バージョン（v1 → v2 に切り替え済み等）は更新しない
    """
    results = []
    has_more = True
    cursor = None

    async with httpx.AsyncClient(timeout=30) as client:
        while has_more:
            payload: dict = {
                "filter": {
                    "and": [
                        {
                            "property": "DB",
                            "multi_select": {"contains": "Linkedin"},
                        },
                        {
                            "property": "使用中",
                            "checkbox": {"equals": True},
                        },
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


def get_code_from_scout_page(page: dict) -> str | None:
    """
    Notion スカウトDB レコードからポジションコードを抽出する。

    識別ID (title) の先頭セグメントを使う。
    例:
      "GFT-CISO-v1"        → "GFT"
      "BST-BizDev-v2"      → "BST"
      "MDY_COO候補_v1"     → "MDY"
    """
    props = page.get("properties", {})

    # ① 識別ID（title）から抽出
    title_parts = props.get("識別ID", {}).get("title", [])
    title = "".join(t.get("plain_text", "") for t in title_parts)
    if title:
        m = re.match(r"^([A-Z]{2,4})[\-_]", title)
        if m:
            return m.group(1)
        # ハイフン等がない場合も試みる
        m = re.match(r"^([A-Z]{2,4})\b", title)
        if m:
            return m.group(1)

    return None


def get_current_sent_count(page: dict) -> int:
    """Notion ページから現在の送信数を取得する（なければ 0）"""
    return page.get("properties", {}).get("送信数", {}).get("number") or 0


async def add_to_sent_count(page_id: str, new_total: int) -> None:
    """指定 Notion ページの「送信数」を更新する"""
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
    ts = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] LinkedIn → Notion 送信数同期開始（対象日: {today}）")

    if DRY_RUN:
        print("⚠️  --dry-run モード: Notion の更新は行いません")

    # ── 二重実行チェック ──────────────────────────────────────────────────────
    if already_synced_today() and not FORCE:
        print(f"✅ 本日 ({today}) は既に同期済みです。スキップします。")
        print("   強制実行する場合は --force オプションを付けてください。")
        return

    # ── ① LinkedIn から今日の送信数を取得 ───────────────────────────────────
    print("\n📥 LinkedIn Recruiter から今日の送信数を取得中...")
    try:
        today_counts = await scrape_today_sent_counts()
    except Exception as e:
        print(f"❌ LinkedIn スクレイピング失敗: {e}")
        sys.exit(1)

    if not today_counts:
        print("ℹ️  今日の送信データが見つかりませんでした（送信なし or セレクター要調整）。")
        save_state(today, {})
        return

    print(f"📊 取得結果（コード: 今日の送信数）:")
    for code, cnt in sorted(today_counts.items()):
        print(f"   {code}: {cnt} 件")

    # ── ② Notion スカウトDB を取得 ───────────────────────────────────────────
    print("\n📋 Notion スカウトDB を取得中...")
    try:
        scout_pages = await query_scout_db_linkedin()
    except Exception as e:
        print(f"❌ Notion DB 取得失敗: {e}")
        sys.exit(1)

    print(f"   {len(scout_pages)} 件の LinkedIn レコードを取得")

    # ── ③ マッチング → 加算更新 ─────────────────────────────────────────────
    print("\n🔄 マッチングして更新中...")
    updated: dict[str, int] = {}   # {code: 加算した数}
    unmatched_codes = set(today_counts.keys())

    for page in scout_pages:
        page_id = page["id"]
        code = get_code_from_scout_page(page)

        if not code or code not in today_counts:
            continue

        unmatched_codes.discard(code)
        add_count = today_counts[code]
        current   = get_current_sent_count(page)
        new_total = current + add_count

        title_parts = page.get("properties", {}).get("識別ID", {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_parts)

        if DRY_RUN:
            print(f"   [dry-run] [{code}] 識別ID={title!r}: {current} + {add_count} = {new_total}")
        else:
            await add_to_sent_count(page_id, new_total)
            print(f"   ✅ [{code}] 識別ID={title!r}: {current} + {add_count} → {new_total}")
            updated[code] = add_count

    # マッチしなかったコードを警告
    if unmatched_codes:
        print(f"\n⚠️  Notion にマッチするレコードが見つからなかったコード: {sorted(unmatched_codes)}")
        print("   → LinkedIn プロジェクト名とスカウトDB 識別ID の先頭コードを確認してください。")

    # ── ④ 状態保存 ───────────────────────────────────────────────────────────
    if not DRY_RUN:
        save_state(today, updated)

    ts_end = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts_end}] 完了: {len(updated)} ポジション更新")


if __name__ == "__main__":
    if "--save-session" in sys.argv:
        asyncio.run(save_session())
    else:
        asyncio.run(main())
