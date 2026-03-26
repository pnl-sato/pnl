"""
LinkedIn Recruiter 送信数 → Notion スカウトDB 同期スクリプト

【概要】
LinkedIn Recruiter の送信済みメッセージ履歴をスクレイピングし、
Notion スカウトDB の「送信数」フィールドを上書き更新する。

【前提】
- LinkedIn Recruiter の「送信済み InMail」画面から件数を取得する
- Notion スカウトDB の識別ID に含まれるポジションコード（例: GFT, BST）で紐付ける
- 1日1回 cron で実行することを想定

【初回セットアップ】
  pip install -r requirements.txt
  playwright install chromium
  cp .env.example .env  # 編集して NOTION_TOKEN を設定
  python linkedin_notion_sync.py --save-session  # ブラウザでログインしてセッション保存

【定期実行（cron）例】
  0 9 * * * cd /path/to/pnl && python scripts/linkedin_notion_sync.py >> logs/sync.log 2>&1
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# ─── 設定 ────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
# Notion スカウトDB のデータベースページID（ダッシュなし形式 or UUID形式どちらでも可）
# collection://2597d017-b6a0-801b-8185-000ba4b9661e に対応する database ID
SCOUT_DB_ID = os.environ.get("SCOUT_DB_ID", "2597d017b6a0808ea499c4ec941d2a96")

SESSION_FILE = Path(__file__).parent / "linkedin_session.json"

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# ─── LinkedIn スクレイピング ───────────────────────────────────────────────────

async def save_session():
    """ブラウザを起動してログイン → セッションを保存する（初回のみ実行）"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=500)
        context = await browser.new_context()
        page = await context.new_page()

        print("=== LinkedIn にログインしてください ===")
        print("ログイン完了後、LinkedIn Recruiter のトップページまで進めてください。")
        await page.goto("https://www.linkedin.com/login")

        # ユーザーが手動でログインするまで待機（最大5分）
        await page.wait_for_url("**/talent/**", timeout=300_000)
        print("ログイン完了。セッションを保存します...")

        storage = await context.storage_state()
        SESSION_FILE.write_text(json.dumps(storage, ensure_ascii=False, indent=2))
        print(f"セッション保存: {SESSION_FILE}")

        await browser.close()


async def scrape_sent_counts() -> dict[str, int]:
    """
    LinkedIn Recruiter の送信済みメッセージ数をポジションコード別に集計して返す。

    Returns:
        {ポジションコード: 送信数} の辞書
        例: {"GFT": 45, "BST": 23, "MDY": 12}

    【重要】
    LinkedIn Recruiter の UI は変更される場合があります。
    うまく取得できない場合は --debug フラグを使いセレクターを調整してください。
    """
    if not SESSION_FILE.exists():
        raise FileNotFoundError(
            f"セッションファイルが見つかりません: {SESSION_FILE}\n"
            "先に `python linkedin_notion_sync.py --save-session` を実行してください。"
        )

    storage = json.loads(SESSION_FILE.read_text())

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless="--debug" not in sys.argv,
            slow_mo=300 if "--debug" in sys.argv else 0,
        )
        context = await browser.new_context(storage_state=storage)
        page = await context.new_page()

        # ① LinkedIn Recruiter にアクセス
        await page.goto("https://www.linkedin.com/talent/")
        if "login" in page.url:
            raise RuntimeError(
                "セッションが切れています。`--save-session` で再ログインしてください。"
            )

        # ② 送信済みメッセージ一覧ページに移動
        # LinkedIn Recruiter の Messaging → Sent を開く
        # ※ URL は環境によって異なる場合あり
        await page.goto("https://www.linkedin.com/talent/inbox?filterBy=sent")
        await page.wait_for_load_state("networkidle", timeout=15_000)

        sent_counts: dict[str, int] = {}
        page_num = 0

        while True:
            page_num += 1
            print(f"  ページ {page_num} を取得中...", flush=True)

            # ③ メッセージ一覧のアイテムを取得
            # 実際のセレクターは LinkedIn の UI に合わせて調整が必要
            message_items = await page.query_selector_all(
                "[data-control-name='message_list_item'], "
                ".msg-conversation-listitem, "
                ".message-list-item"
            )

            if not message_items:
                # セレクターが合っていない場合のフォールバック: テキスト全体から抽出
                body_text = await page.inner_text("body")
                print("  ⚠️  メッセージ要素が見つかりません。--debug モードで確認してください。")
                print(f"     ページテキスト（先頭500文字）:\n{body_text[:500]}")
                break

            for item in message_items:
                text = await item.inner_text()
                # ポジションコードを抽出（例: #GFT, #BST など）
                # スカウトDB の識別IDや本文に含まれるコードでマッチング
                codes = extract_position_codes(text)
                for code in codes:
                    sent_counts[code] = sent_counts.get(code, 0) + 1

            # ④ 次のページへ
            next_btn = await page.query_selector(
                "[aria-label='Next'], .artdeco-pagination__button--next"
            )
            if next_btn and await next_btn.is_enabled():
                await next_btn.click()
                await page.wait_for_load_state("networkidle", timeout=10_000)
            else:
                break

        await context.storage_state(path=str(SESSION_FILE))  # セッション更新
        await browser.close()

    return sent_counts


def extract_position_codes(text: str) -> list[str]:
    """
    テキストからポジションコードを抽出する。

    ポジションコードの命名規則（Notion スカウトDB 識別ID より）:
    - 「#GFT」「#BST」のような大文字アルファベット2〜4文字
    - または「GFT-」「BST-」のようなプレフィックス形式

    ※ 実際のスカウト文の中にポジションコードが含まれている前提。
    　 含まれていない場合は、別のマッチング方法（メッセージタイトル等）に変更してください。
    """
    # 例: "#GFT", "GFT-", "[GFT]" 等のパターンでコードを検出
    patterns = [
        r"#([A-Z]{2,4})\b",           # #GFT スタイル
        r"\b([A-Z]{2,4})-",           # GFT- スタイル
        r"\[([A-Z]{2,4})\]",          # [GFT] スタイル
    ]
    codes = set()
    for pattern in patterns:
        codes.update(re.findall(pattern, text))
    return list(codes)


# ─── Notion 更新 ─────────────────────────────────────────────────────────────

async def query_scout_db() -> list[dict]:
    """
    Notion スカウトDB から LinkedIn 用のレコードを全件取得する。

    Returns:
        Notion ページオブジェクトのリスト
    """
    results = []
    has_more = True
    cursor = None

    async with httpx.AsyncClient() as client:
        while has_more:
            payload: dict = {
                "filter": {
                    "property": "DB",
                    "multi_select": {"contains": "Linkedin"},
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


def get_position_code_from_page(page: dict) -> str | None:
    """
    Notion ページオブジェクトからポジションコードを抽出する。

    スカウトDB の 識別ID（title）に含まれるコードで判定。
    例: 識別ID = "GFT-CISO-v1-LinkedIn" → "GFT"
    """
    props = page.get("properties", {})
    title_prop = props.get("識別ID", {})
    title_parts = title_prop.get("title", [])
    title = "".join(t.get("plain_text", "") for t in title_parts)

    # 識別IDの先頭コードを抽出
    m = re.match(r"^([A-Z]{2,4})[^A-Z]", title)
    if m:
        return m.group(1)

    # コード列プロパティ（rollup）があればそちらも確認
    code_rollup = props.get("クライアント", {})  # rollupのプロパティ名に合わせて変更
    # ... rollup の値取得ロジック（必要に応じて追加）

    return None


async def update_sent_count(page_id: str, count: int) -> None:
    """
    指定 Notion ページの「送信数」フィールドを更新する。
    """
    async with httpx.AsyncClient() as client:
        res = await client.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers=NOTION_HEADERS,
            json={
                "properties": {
                    "送信数": {"number": count},
                }
            },
        )
        res.raise_for_status()


# ─── メイン処理 ───────────────────────────────────────────────────────────────

async def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] LinkedIn → Notion 同期開始")

    # ① LinkedIn から送信数を取得
    print("LinkedIn Recruiter から送信数を取得中...")
    try:
        sent_counts = await scrape_sent_counts()
    except Exception as e:
        print(f"❌ LinkedIn スクレイピング失敗: {e}")
        sys.exit(1)

    if not sent_counts:
        print("⚠️  送信数データが取得できませんでした。終了します。")
        sys.exit(0)

    print(f"取得したポジション別送信数: {sent_counts}")

    # ② Notion スカウトDB を取得
    print("Notion スカウトDB を取得中...")
    try:
        scout_pages = await query_scout_db()
    except Exception as e:
        print(f"❌ Notion DB 取得失敗: {e}")
        sys.exit(1)

    print(f"{len(scout_pages)} 件の LinkedIn スカウトレコードを取得")

    # ③ マッチングして更新
    updated = 0
    skipped = 0

    for page in scout_pages:
        page_id = page["id"]
        code = get_position_code_from_page(page)

        if not code:
            skipped += 1
            continue

        if code not in sent_counts:
            skipped += 1
            continue

        new_count = sent_counts[code]
        # 現在値を取得
        current = page["properties"].get("送信数", {}).get("number") or 0

        if current == new_count:
            print(f"  [{code}] 変更なし ({current}件) → スキップ")
            continue

        await update_sent_count(page_id, new_count)
        print(f"  [{code}] {current} → {new_count} 件に更新")
        updated += 1

    print(
        f"\n完了: {updated} 件更新, {skipped} 件スキップ "
        f"({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
    )


if __name__ == "__main__":
    if "--save-session" in sys.argv:
        asyncio.run(save_session())
    else:
        asyncio.run(main())
