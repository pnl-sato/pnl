"""
日本IT企業 現任CISO Notion 自動登録スクリプト

【概要】
CSVファイルから日本のIT企業に在籍する現任CISOの情報を読み込み、
Notion の以下3つの DB に自動登録する。

  1. 企業DB    - 在籍企業が未登録の場合は新規作成（カテゴリ=在籍企業）
  2. Person DB - 人物が未登録の場合は新規作成
  3. CISO DB   - CISO エントリを作成（Person DB とリレーション）

【CSVフォーマット】(data/ciso_list.csv)
  必須列 : 名前, 企業名
  任意列 : ポジション名, 上場_非上場, 事業ドメイン, 事業モデル, 従業員規模,
           年齢, 入社年月, サマリ

  上場_非上場 の入力値例: 上場（東証プライム）, 上場（グロース）, 上場（スタンダード）, 非上場
  事業ドメイン / 事業モデル : 「・」区切りで複数指定可
    例) 事業ドメイン: IT・SaaS・サイバーセキュリティ
  入社年月 : YYYY/MM または YYYY-MM 形式

【実行例】
  python scripts/register_ciso.py
  python scripts/register_ciso.py --csv data/ciso_list.csv
  python scripts/register_ciso.py --dry-run

【セットアップ】
  cp .env.example .env   # NOTION_TOKEN を記入
  pip install -r scripts/requirements.txt
"""

import asyncio
import csv
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

# ─── 設定 ────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_TOKEN = os.environ["NOTION_TOKEN"]

# Notion データベース ID（ダッシュなし32文字）
COMPANY_DB_ID = os.environ.get("COMPANY_DB_ID", "1fb7d017b6a080cc9f5dcd66d0320614")  # 企業DB
PERSON_DB_ID  = os.environ.get("PERSON_DB_ID",  "2cd7d017b6a08044bce2e5b790684a6a")  # Person DB
CISO_DB_ID    = os.environ.get("CISO_DB_ID",    "2cd7d017b6a080e78b7ad14daf145f98")  # CISO DB

JST = ZoneInfo("Asia/Tokyo")

DEFAULT_CSV = Path(__file__).parent.parent / "data" / "ciso_list.csv"

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

DRY_RUN = "--dry-run" in sys.argv

# 上場/非上場の正規化マップ
LISTING_NORMALIZE: dict[str, str] = {
    "上場":              "上場（東証プライム）",
    "東証プライム":      "上場（東証プライム）",
    "上場（東証プライム）": "上場（東証プライム）",
    "プライム":          "上場（東証プライム）",
    "グロース":          "上場（グロース）",
    "上場（グロース）":  "上場（グロース）",
    "スタンダード":      "上場（スタンダード）",
    "上場（スタンダード）": "上場（スタンダード）",
    "非上場":            "非上場",
    "未上場":            "非上場",
}

# 従業員規模の有効値
EMPLOYEE_SCALE_OPTIONS = {
    "〜50名", "50〜200名", "200〜500名",
    "500〜1000名", "1000〜3000名", "3000〜5000名", "5000名以上",
}


# ─── CSV 読み込み ─────────────────────────────────────────────────────────────

def read_csv(filepath: Path) -> list[dict]:
    """CISO リスト CSV を読み込む"""
    if not filepath.exists():
        raise FileNotFoundError(
            f"CSVファイルが見つかりません: {filepath}\n"
            f"data/ciso_list.csv を作成するか --csv でパスを指定してください。"
        )
    rows = []
    with open(filepath, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not any(row.values()):
                continue  # 空行スキップ
            rows.append({k.strip(): v.strip() for k, v in row.items()})
    return rows


# ─── Notion: 企業DB ───────────────────────────────────────────────────────────

async def find_company(client: httpx.AsyncClient, name: str) -> dict | None:
    """企業DB から企業名で完全一致検索"""
    res = await client.post(
        f"{NOTION_API}/databases/{COMPANY_DB_ID}/query",
        headers=NOTION_HEADERS,
        json={"filter": {"property": "企業名", "title": {"equals": name}}},
    )
    res.raise_for_status()
    results = res.json().get("results", [])
    return results[0] if results else None


async def create_company(
    client: httpx.AsyncClient,
    name: str,
    listing: str | None = None,
    domains: list[str] | None = None,
    models: list[str] | None = None,
    employee_scale: str | None = None,
) -> dict:
    """企業DB に新規企業を作成"""
    properties: dict = {
        "企業名": {"title": [{"text": {"content": name}}]},
        "カテゴリ": {"select": {"name": "在籍企業"}},
        "サイバーセキュリティ関連企業": {"checkbox": False},
    }

    if listing:
        listing_val = LISTING_NORMALIZE.get(listing, listing)
        properties["上場／非上場"] = {"select": {"name": listing_val}}

    if domains:
        properties["事業ドメイン"] = {
            "multi_select": [{"name": d} for d in domains if d]
        }

    if models:
        properties["事業モデル"] = {
            "multi_select": [{"name": m} for m in models if m]
        }

    if employee_scale and employee_scale in EMPLOYEE_SCALE_OPTIONS:
        properties["従業員規模"] = {"select": {"name": employee_scale}}

    res = await client.post(
        f"{NOTION_API}/pages",
        headers=NOTION_HEADERS,
        json={
            "parent": {"database_id": COMPANY_DB_ID},
            "properties": properties,
        },
    )
    res.raise_for_status()
    return res.json()


# ─── Notion: Person DB ────────────────────────────────────────────────────────

async def find_person(client: httpx.AsyncClient, name: str) -> dict | None:
    """Person DB から氏名で完全一致検索"""
    res = await client.post(
        f"{NOTION_API}/databases/{PERSON_DB_ID}/query",
        headers=NOTION_HEADERS,
        json={"filter": {"property": "名前", "title": {"equals": name}}},
    )
    res.raise_for_status()
    results = res.json().get("results", [])
    return results[0] if results else None


async def create_person(
    client: httpx.AsyncClient,
    name: str,
    company_page_id: str,
    pos_name: str | None = None,
    age: int | None = None,
    join_date: str | None = None,
    summary: str | None = None,
) -> dict:
    """Person DB に新規人物を作成"""
    properties: dict = {
        "名前": {"title": [{"text": {"content": name}}]},
        "在籍企業": {"relation": [{"id": company_page_id}]},
    }

    if pos_name:
        properties["ポジション名"] = {"rich_text": [{"text": {"content": pos_name}}]}

    if age is not None:
        properties["年齢"] = {"number": age}

    if join_date:
        # YYYY/MM または YYYY-MM → YYYY-MM-01 に正規化
        join_clean = join_date.replace("/", "-")
        if len(join_clean) == 7:
            join_clean += "-01"
        properties["入社年月"] = {"date": {"start": join_clean}}

    if summary:
        properties["サマリ"] = {"rich_text": [{"text": {"content": summary}}]}

    res = await client.post(
        f"{NOTION_API}/pages",
        headers=NOTION_HEADERS,
        json={
            "parent": {"database_id": PERSON_DB_ID},
            "properties": properties,
        },
    )
    res.raise_for_status()
    return res.json()


# ─── Notion: CISO DB ──────────────────────────────────────────────────────────

async def find_ciso_entry(client: httpx.AsyncClient, name: str) -> dict | None:
    """CISO DB から氏名で完全一致検索"""
    res = await client.post(
        f"{NOTION_API}/databases/{CISO_DB_ID}/query",
        headers=NOTION_HEADERS,
        json={"filter": {"property": "名前", "title": {"equals": name}}},
    )
    res.raise_for_status()
    results = res.json().get("results", [])
    return results[0] if results else None


async def create_ciso_entry(
    client: httpx.AsyncClient,
    name: str,
    person_page_id: str,
) -> dict:
    """CISO DB に新規エントリを作成"""
    res = await client.post(
        f"{NOTION_API}/pages",
        headers=NOTION_HEADERS,
        json={
            "parent": {"database_id": CISO_DB_ID},
            "properties": {
                "名前": {"title": [{"text": {"content": name}}]},
                "Person": {"relation": [{"id": person_page_id}]},
            },
        },
    )
    res.raise_for_status()
    return res.json()


# ─── 1 件処理 ─────────────────────────────────────────────────────────────────

async def process_one(
    client: httpx.AsyncClient,
    row: dict,
    stats: dict,
) -> None:
    """CSV の 1 行を処理して Notion に登録する"""
    name         = row.get("名前", "").strip()
    company_name = row.get("企業名", "").strip()

    if not name or not company_name:
        print(f"  ⚠️  スキップ（名前または企業名が空）: {row}")
        stats["skipped"] += 1
        return

    pos_name    = row.get("ポジション名") or "CISO"
    listing     = row.get("上場_非上場") or row.get("上場/非上場") or None
    domains_raw = row.get("事業ドメイン") or ""
    models_raw  = row.get("事業モデル") or ""
    emp_scale   = row.get("従業員規模") or None
    age_raw     = row.get("年齢") or None
    join_date   = row.get("入社年月") or None
    summary     = row.get("サマリ") or None

    domains = [d.strip() for d in domains_raw.split("・") if d.strip()]
    models  = [m.strip() for m in models_raw.split("・") if m.strip()]
    age     = int(age_raw) if age_raw and str(age_raw).isdigit() else None

    print(f"  👤 {name}  /  {company_name}  /  {pos_name}")

    # ① 企業DB: 既存検索 → なければ作成
    company_page = await find_company(client, company_name)
    if company_page:
        company_page_id = company_page["id"]
        print(f"     企業     : 既存  ({company_name})")
        stats["company_exists"] += 1
    else:
        if DRY_RUN:
            print(f"     企業     : [dry-run] 新規作成予定  ({company_name})")
            stats["company_created"] += 1
            stats["person_created"] += 1
            stats["ciso_created"] += 1
            return
        company_page = await create_company(
            client, company_name, listing, domains, models, emp_scale
        )
        company_page_id = company_page["id"]
        print(f"     企業     : ✅ 新規作成  ({company_name})")
        stats["company_created"] += 1

    # ② Person DB: 既存検索 → なければ作成
    person_page = await find_person(client, name)
    if person_page:
        person_page_id = person_page["id"]
        print(f"     Person   : 既存  ({name})")
        stats["person_exists"] += 1
    else:
        if DRY_RUN:
            print(f"     Person   : [dry-run] 新規作成予定  ({name})")
            stats["person_created"] += 1
            stats["ciso_created"] += 1
            return
        person_page = await create_person(
            client, name, company_page_id, pos_name, age, join_date, summary
        )
        person_page_id = person_page["id"]
        print(f"     Person   : ✅ 新規作成  ({name})")
        stats["person_created"] += 1

    # ③ CISO DB: 既存検索 → なければ作成
    ciso_entry = await find_ciso_entry(client, name)
    if ciso_entry:
        print(f"     CISO DB  : 既存  ({name})")
        stats["ciso_exists"] += 1
    else:
        if DRY_RUN:
            print(f"     CISO DB  : [dry-run] 新規作成予定  ({name})")
            stats["ciso_created"] += 1
            return
        await create_ciso_entry(client, name, person_page_id)
        print(f"     CISO DB  : ✅ 新規作成  ({name})")
        stats["ciso_created"] += 1


# ─── メイン ───────────────────────────────────────────────────────────────────

async def main() -> None:
    # CSV パスを引数から取得
    csv_path = DEFAULT_CSV
    for i, arg in enumerate(sys.argv):
        if arg == "--csv" and i + 1 < len(sys.argv):
            csv_path = Path(sys.argv[i + 1])
            break

    ts = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] 日本IT企業 CISO Notion 自動登録 開始")
    print(f"  CSV: {csv_path}")

    if DRY_RUN:
        print("⚠️  --dry-run モード: Notion への書き込みは行いません\n")

    rows = read_csv(csv_path)
    print(f"  対象: {len(rows)} 件\n")

    stats: dict[str, int] = {
        "company_exists": 0, "company_created": 0,
        "person_exists":  0, "person_created":  0,
        "ciso_exists":    0, "ciso_created":    0,
        "skipped":        0,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        for i, row in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}]")
            try:
                await process_one(client, row, stats)
            except Exception as e:
                name = row.get("名前", "?")
                print(f"  ❌ エラー ({name}): {e}")
                stats["skipped"] += 1

    ts_end = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts_end}] 完了")
    print(f"  企業DB    : 新規 {stats['company_created']} 件 / 既存 {stats['company_exists']} 件")
    print(f"  Person DB : 新規 {stats['person_created']} 件 / 既存 {stats['person_exists']} 件")
    print(f"  CISO DB   : 新規 {stats['ciso_created']} 件 / 既存 {stats['ciso_exists']} 件")
    print(f"  スキップ  : {stats['skipped']} 件")


if __name__ == "__main__":
    asyncio.run(main())
