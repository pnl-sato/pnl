"""
ポジション DB の「JD URL」フィールドを自動生成・更新するスクリプト

【概要】
オープン中のポジションに対して、Claude API でポジション名を英訳してスラッグを生成し、
Notion の「JD URL」フィールドに候補者共有用の URL を書き込む。

【URL 形式】
  https://pnl.notion.site/jd-{クライアントコード}-{役職スラッグ}
  例: https://pnl.notion.site/jd-gtf-ciso

  ※ 実際の Notion ページの公開 URL 設定は UI で手動変更が必要（API 非対応）
  　 このスクリプトは「JD URL」フィールドへの推奨 URL の書き込みと一覧出力を行う。

【前提】
  - .env に NOTION_TOKEN / POSITION_DB_ID / ANTHROPIC_API_KEY が設定されていること
  - ポジション DB の「コード」ロールアップ（企業 DB から）に値が入っていること

【セットアップ】
  pip install -r requirements.txt
  cp .env.example .env  # 各トークンを記入

【実行オプション】
  --dry-run       Notion を更新せず、生成された URL の一覧を表示するだけ
  --force         JD URL が既に設定済みのポジションも上書き
  --add-property  「JD URL」プロパティを DB に追加してから実行（初回のみ必要）
"""

import asyncio
import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

# ─── 設定 ──────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_TOKEN      = os.environ["NOTION_TOKEN"]
POSITION_DB_ID    = os.environ["POSITION_DB_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_HEADERS = {
    "x-api-key": ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}

NOTION_SITE_BASE = "https://pnl.notion.site"
JD_URL_PROPERTY  = "JD URL"

DRY_RUN      = "--dry-run"      in sys.argv
FORCE        = "--force"        in sys.argv
ADD_PROPERTY = "--add-property" in sys.argv


# ─── Notion ヘルパー ────────────────────────────────────────────────────────────

def extract_title(properties: dict, key: str = "名前") -> str:
    """title プロパティからテキストを取得"""
    parts = properties.get(key, {}).get("title", [])
    return "".join(p.get("plain_text", "") for p in parts).strip()


def extract_rollup_text(properties: dict, key: str = "コード") -> str:
    """
    rollup プロパティ（show_original / array 型）から最初の rich_text を取得。
    企業 DB の「コード」ロールアップを想定。
    """
    rollup = properties.get(key, {}).get("rollup", {})
    if rollup.get("type") == "array":
        for item in rollup.get("array", []):
            # rich_text 形式
            if item.get("type") == "rich_text":
                parts = item.get("rich_text", [])
                text = "".join(p.get("plain_text", "") for p in parts).strip()
                if text:
                    return text
            # title 形式（企業名が title の場合）
            if item.get("type") == "title":
                parts = item.get("title", [])
                text = "".join(p.get("plain_text", "") for p in parts).strip()
                if text:
                    return text
    return ""


def extract_url(properties: dict, key: str = "JD URL") -> str:
    """url プロパティから値を取得"""
    return properties.get(key, {}).get("url") or ""


def to_slug(text: str) -> str:
    """テキストを URL スラッグに変換（英小文字・数字・ハイフンのみ）"""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


# ─── Notion API ────────────────────────────────────────────────────────────────

async def add_jd_url_property(client: httpx.AsyncClient) -> None:
    """ポジション DB に「JD URL」プロパティ（url 型）を追加する"""
    resp = await client.patch(
        f"{NOTION_API}/databases/{POSITION_DB_ID}",
        headers=NOTION_HEADERS,
        json={"properties": {JD_URL_PROPERTY: {"url": {}}}},
    )
    if resp.status_code == 200:
        print(f"✓ 「{JD_URL_PROPERTY}」プロパティをポジション DB に追加しました")
    else:
        print(f"✗ プロパティ追加失敗: {resp.status_code} {resp.text}")
        resp.raise_for_status()


async def query_open_positions(client: httpx.AsyncClient) -> list[dict]:
    """ステータスが「オープン」のポジションをすべて取得"""
    pages: list[dict] = []
    cursor: str | None = None

    while True:
        body: dict = {
            "filter": {
                "property": "ステータス",
                "status": {"equals": "オープン"},
            },
            "page_size": 100,
        }
        if cursor:
            body["start_cursor"] = cursor

        resp = await client.post(
            f"{NOTION_API}/databases/{POSITION_DB_ID}/query",
            headers=NOTION_HEADERS,
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        pages.extend(data["results"])

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return pages


async def update_jd_url_field(
    client: httpx.AsyncClient, page_id: str, url: str
) -> None:
    """ポジションページの「JD URL」フィールドを更新"""
    resp = await client.patch(
        f"{NOTION_API}/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": {JD_URL_PROPERTY: {"url": url}}},
        timeout=30,
    )
    resp.raise_for_status()


# ─── Claude API ────────────────────────────────────────────────────────────────

async def generate_slugs(
    client: httpx.AsyncClient, position_names: list[str]
) -> list[str]:
    """
    ポジション名（日本語）を英語 URL スラッグに一括変換。
    Claude haiku に1回のリクエストで全件処理させる。
    """
    if not position_names:
        return []

    numbered = "\n".join(f"{i+1}. {name}" for i, name in enumerate(position_names))
    prompt = f"""\
以下の採用ポジション名（日本語）を、それぞれ英語の URL スラッグに変換してください。

ルール:
- 英小文字・数字・ハイフンのみ使用
- できるだけ短く、意味が伝わるようにする（最大 4 単語程度）
- 役職・機能を表す一般的な英語を使う
- 番号と対応させて、番号. スラッグ の形式で出力する（説明不要）

例:
- セキュリティ統括責任者（CISO候補） → ciso
- セキュリティマネージャー → security-manager
- 事業開発責任者 → head-of-bizdev
- プロダクトマネージャー（エンタープライズ） → product-manager
- 経営企画部長 → corporate-planning-director

ポジション名:
{numbered}
"""

    resp = await client.post(
        ANTHROPIC_API_URL,
        headers=ANTHROPIC_HEADERS,
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["content"][0]["text"].strip()

    # 出力を番号順にパース: "1. ciso" → "ciso"
    slugs: dict[int, str] = {}
    for line in content.splitlines():
        m = re.match(r"^\s*(\d+)\.\s*([a-z0-9][a-z0-9\-]*)", line)
        if m:
            idx = int(m.group(1)) - 1
            slugs[idx] = m.group(2).strip("-")

    # 変換できなかった分はフォールバック（ポジション名を ASCII に）
    result = []
    for i, name in enumerate(position_names):
        slug = slugs.get(i) or to_slug(re.sub(r"[^\w\s-]", "", name))
        result.append(slug)
    return result


# ─── メイン ────────────────────────────────────────────────────────────────────

async def main() -> None:
    async with httpx.AsyncClient() as client:

        # --add-property: JD URL プロパティを DB に追加
        if ADD_PROPERTY:
            await add_jd_url_property(client)

        # オープン中のポジションを取得
        print("ポジション DB を取得中...")
        pages = await query_open_positions(client)
        print(f"  オープン中: {len(pages)} 件")

        if not pages:
            print("対象ポジションがありません。")
            return

        # 処理対象を絞り込み（--force がなければ JD URL 未設定のみ）
        targets = []
        for page in pages:
            props    = page["properties"]
            name     = extract_title(props)
            code     = extract_rollup_text(props)
            existing = extract_url(props, JD_URL_PROPERTY)

            if not name:
                print(f"  スキップ（名前なし）: {page['id']}")
                continue
            if not code:
                print(f"  スキップ（コードなし）: {name}")
                continue
            if existing and not FORCE:
                print(f"  スキップ（設定済み）: {name} → {existing}")
                continue

            targets.append({"page_id": page["id"], "name": name, "code": code})

        if not targets:
            print("更新対象ポジションがありません。（--force で上書き可能）")
            return

        print(f"\n翻訳対象: {len(targets)} 件")

        # Claude API でスラッグを一括生成
        print("Claude API でスラッグを生成中...")
        names = [t["name"] for t in targets]
        slugs = await generate_slugs(client, names)

        # URL を構築して Notion に反映
        print()
        update_tasks = []
        for item, slug in zip(targets, slugs):
            code_slug = to_slug(item["code"])
            url = f"{NOTION_SITE_BASE}/jd-{code_slug}-{slug}"
            item["url"] = url

            status = "[DRY-RUN]" if DRY_RUN else "→ 更新"
            print(f"  {status} {item['name']}")
            print(f"           {url}")

            if not DRY_RUN:
                update_tasks.append(
                    update_jd_url_field(client, item["page_id"], url)
                )

        if not DRY_RUN:
            print("\nNotion を更新中...")
            await asyncio.gather(*update_tasks)
            print(f"✓ {len(targets)} 件を更新しました")
        else:
            print(f"\n（--dry-run モード: Notion は更新されていません）")


if __name__ == "__main__":
    asyncio.run(main())
