"""
Salesforce 候補者 → Notion 候補者DB 同期スクリプト

【概要】
Salesforce の Contact レコード（候補者）を Notion 候補者DB に同期する。
・新規レコード  : Notion に新規ページとして作成する
・既存レコード  : --update フラグ付きで上書き更新する（SalesForce URL で照合）

【Salesforce フィールド設定】
スクリプト内の「カスタムフィールド設定」セクション、または .env の
SF_FIELD_* 変数をご自身の SF 組織のフィールド名に合わせて編集してください。

【セットアップ】
  pip install -r requirements.txt
  cp .env.example .env
  # NOTION_TOKEN, SF_USERNAME, SF_PASSWORD, SF_SECURITY_TOKEN を .env に記入

【実行オプション】
  --dry-run         Notion を更新せず確認のみ
  --update          既存の Notion ページも更新する（デフォルト: 新規のみ）
  --force           前回同期日時を無視して全件処理（デフォルト: 7日前以降）
  --debug           詳細ログ出力
  --since DATETIME  この日時以降の更新を対象にする（例: 2026-03-01T00:00:00）

【cron 例】毎時実行
  0 * * * * cd /path/to/pnl && python scripts/salesforce_notion_sync.py >> logs/sf_sync.log 2>&1
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from simple_salesforce import Salesforce

# ─── 設定 ────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_TOKEN    = os.environ["NOTION_TOKEN"]
# 候補者DB の Notion database ID
# collection://2057d017-b6a0-80fc-9e8d-000b3b6ab37e → ダッシュなし32文字
CANDIDATE_DB_ID = os.environ.get("CANDIDATE_DB_ID", "2057d017b6a080fc9e8d000b3b6ab37e")

SF_USERNAME       = os.environ["SF_USERNAME"]
SF_PASSWORD       = os.environ["SF_PASSWORD"]
SF_SECURITY_TOKEN = os.environ.get("SF_SECURITY_TOKEN", "")
SF_DOMAIN         = os.environ.get("SF_DOMAIN", "login")  # sandbox の場合は "test"

STATE_FILE = Path(__file__).parent / "sf_sync_state.json"
JST = ZoneInfo("Asia/Tokyo")

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

DRY_RUN = "--dry-run" in sys.argv
UPDATE   = "--update"  in sys.argv
FORCE    = "--force"   in sys.argv
DEBUG    = "--debug"   in sys.argv

# --since DATETIME 引数のパース
_SINCE_ARG: str | None = None
for _i, _arg in enumerate(sys.argv):
    if _arg == "--since" and _i + 1 < len(sys.argv):
        _SINCE_ARG = sys.argv[_i + 1]
        break


# ─── Salesforce カスタムフィールド設定 ────────────────────────────────────────
#
# ご自身の Salesforce 組織のカスタムフィールド API 参照名に合わせて変更してください。
# .env の SF_FIELD_* 変数で上書きも可能です。
#
# フィールドが SF 組織に存在しない場合、自動的にスキップされます。
#
# 対応する Notion 候補者DB フィールド:
#   現年収 (number)         ← SF_FIELD_CURRENT_SALARY
#   最低希望年収 (number)   ← SF_FIELD_DESIRED_SALARY
#   転職検討理由 (text)     ← SF_FIELD_JOB_CHANGE_REASON
#   副業希望 (checkbox)     ← SF_FIELD_SIDE_JOB

SF_FIELD_CURRENT_SALARY    = os.environ.get("SF_FIELD_CURRENT_SALARY",    "Current_Salary__c")
SF_FIELD_DESIRED_SALARY    = os.environ.get("SF_FIELD_DESIRED_SALARY",    "Desired_Salary__c")
SF_FIELD_JOB_CHANGE_REASON = os.environ.get("SF_FIELD_JOB_CHANGE_REASON", "Job_Change_Reason__c")
SF_FIELD_SIDE_JOB          = os.environ.get("SF_FIELD_SIDE_JOB",          "Side_Job_Desired__c")


# ─── 状態管理 ─────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(last_sync: str, synced_count: int) -> None:
    STATE_FILE.write_text(
        json.dumps({"last_sync": last_sync, "synced_count": synced_count},
                   ensure_ascii=False, indent=2)
    )


def get_since_datetime() -> str:
    """同期対象の起点日時（ISO8601）を返す"""
    if _SINCE_ARG:
        return _SINCE_ARG if "T" in _SINCE_ARG else f"{_SINCE_ARG}T00:00:00Z"

    if not FORCE:
        state = load_state()
        if state.get("last_sync"):
            return state["last_sync"]

    # 初回 or --force: 7日前から
    dt = datetime.now(timezone.utc) - timedelta(days=7)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Salesforce: 候補者取得 ────────────────────────────────────────────────────

def connect_salesforce() -> Salesforce:
    return Salesforce(
        username=SF_USERNAME,
        password=SF_PASSWORD,
        security_token=SF_SECURITY_TOKEN,
        domain=SF_DOMAIN,
    )


def build_sf_record_url(instance_url: str, record_id: str) -> str:
    return f"{instance_url}/{record_id}"


def fetch_candidates(sf: Salesforce, since: str) -> tuple[list[dict], list[str]]:
    """
    Salesforce から候補者（Contact）を取得する。

    - LastModifiedDate が since 以降のレコードを対象
    - 存在しないカスタムフィールドは自動スキップ
    - 戻り値: (records, available_custom_fields)
    """
    base_fields = ["Id", "FirstName", "LastName", "Birthdate", "Title"]
    custom_field_candidates = [
        SF_FIELD_CURRENT_SALARY,
        SF_FIELD_DESIRED_SALARY,
        SF_FIELD_JOB_CHANGE_REASON,
        SF_FIELD_SIDE_JOB,
    ]

    # 組織に存在するフィールドのみ選択
    all_field_names = {f["name"] for f in sf.Contact.describe()["fields"]}
    available_custom: list[str] = []
    select_fields = base_fields.copy()

    for cf in custom_field_candidates:
        if cf in all_field_names:
            select_fields.append(cf)
            available_custom.append(cf)
        elif DEBUG:
            print(f"  [DEBUG] SF フィールド未検出（スキップ）: {cf!r}")

    fields_str = ", ".join(select_fields)
    soql = (
        f"SELECT {fields_str} "
        f"FROM Contact "
        f"WHERE LastModifiedDate >= {since} "
        f"ORDER BY LastModifiedDate ASC "
        f"LIMIT 500"
    )

    if DEBUG:
        print(f"  [DEBUG] SOQL: {soql}")

    result = sf.query_all(soql)
    return result.get("records", []), available_custom


# ─── Notion: 候補者DB 操作 ─────────────────────────────────────────────────────

async def find_existing_page(sf_url: str, client: httpx.AsyncClient) -> str | None:
    """SalesForce URL で候補者DB を検索。存在すれば page_id を返す"""
    payload = {
        "filter": {
            "property": "SalesForce",
            "url": {"equals": sf_url},
        },
        "page_size": 1,
    }
    res = await client.post(
        f"{NOTION_API}/databases/{CANDIDATE_DB_ID}/query",
        headers=NOTION_HEADERS,
        json=payload,
    )
    res.raise_for_status()
    results = res.json().get("results", [])
    return results[0]["id"] if results else None


def build_notion_properties(
    record: dict,
    sf_url: str,
    available_custom: list[str],
) -> dict:
    """
    SF Contact レコードから Notion プロパティ辞書を構築する。
    値が None のフィールドはプロパティに含めない（Notion の既存値を保持）。
    """
    last_name  = (record.get("LastName")  or "").strip()
    first_name = (record.get("FirstName") or "").strip()
    full_name  = f"{last_name} {first_name}".strip() or None

    props: dict = {}

    if full_name:
        props["名前"] = {"title": [{"text": {"content": full_name}}]}

    props["SalesForce"] = {"url": sf_url}

    if record.get("Birthdate"):
        props["生年月日"] = {"date": {"start": record["Birthdate"]}}

    if record.get("Title"):
        props["ポジション"] = {"rich_text": [{"text": {"content": record["Title"]}}]}

    if SF_FIELD_CURRENT_SALARY in available_custom:
        val = record.get(SF_FIELD_CURRENT_SALARY)
        if val is not None:
            props["現年収"] = {"number": int(val)}

    if SF_FIELD_DESIRED_SALARY in available_custom:
        val = record.get(SF_FIELD_DESIRED_SALARY)
        if val is not None:
            props["最低希望年収"] = {"number": int(val)}

    if SF_FIELD_JOB_CHANGE_REASON in available_custom:
        val = record.get(SF_FIELD_JOB_CHANGE_REASON)
        if val:
            props["転職検討理由"] = {"rich_text": [{"text": {"content": str(val)}}]}

    if SF_FIELD_SIDE_JOB in available_custom:
        val = record.get(SF_FIELD_SIDE_JOB)
        if val is not None:
            props["副業希望"] = {"checkbox": bool(val)}

    return props


async def create_notion_page(properties: dict, client: httpx.AsyncClient) -> str:
    """候補者DB に新規ページを作成して page_id を返す"""
    payload = {
        "parent": {"database_id": CANDIDATE_DB_ID},
        "properties": properties,
    }
    res = await client.post(
        f"{NOTION_API}/pages",
        headers=NOTION_HEADERS,
        json=payload,
    )
    res.raise_for_status()
    return res.json()["id"]


async def update_notion_page(
    page_id: str, properties: dict, client: httpx.AsyncClient
) -> None:
    """Notion ページのプロパティを更新する（名前は更新しない）"""
    update_props = {k: v for k, v in properties.items() if k != "名前"}
    if not update_props:
        return
    res = await client.patch(
        f"{NOTION_API}/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": update_props},
    )
    res.raise_for_status()


# ─── メイン処理 ───────────────────────────────────────────────────────────────

async def main() -> None:
    now = datetime.now(tz=JST)
    ts  = now.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] Salesforce → Notion 候補者DB 同期開始")

    if DRY_RUN:
        print("⚠️  --dry-run モード: Notion の更新は行いません")
    if UPDATE:
        print("ℹ️  --update モード: 既存レコードも更新します")

    since = get_since_datetime()
    print(f"📅 同期対象: {since} 以降に更新されたレコード")

    # ① Salesforce から候補者を取得
    print("\n📥 Salesforce から候補者を取得中...")
    try:
        sf = connect_salesforce()
        records, available_custom = fetch_candidates(sf, since)
        instance_url = sf.base_url.split("/services")[0]
    except Exception as e:
        print(f"❌ Salesforce 接続/取得失敗: {e}")
        sys.exit(1)

    if not records:
        print("ℹ️  新規・更新された候補者が見つかりませんでした。")
        if not DRY_RUN:
            save_state(now.strftime("%Y-%m-%dT%H:%M:%SZ"), 0)
        return

    print(f"   {len(records)} 件取得")
    if available_custom and DEBUG:
        print(f"   [DEBUG] 利用可能なカスタムフィールド: {available_custom}")

    # ② Notion と照合して作成/更新
    print(f"\n🔄 Notion 候補者DB に同期中...")

    created_count = 0
    updated_count = 0
    skipped_count = 0

    async with httpx.AsyncClient(timeout=30) as client:
        for record in records:
            record_id = record["Id"]
            sf_url    = build_sf_record_url(instance_url, record_id)
            last_name  = (record.get("LastName")  or "").strip()
            first_name = (record.get("FirstName") or "").strip()
            name = f"{last_name} {first_name}".strip() or record_id

            if DEBUG:
                print(f"  [DEBUG] 処理中: {name!r} ({sf_url})")

            try:
                existing_page_id = await find_existing_page(sf_url, client)
            except Exception as e:
                print(f"  ⚠️  Notion 検索失敗 ({name}): {e}")
                skipped_count += 1
                continue

            properties = build_notion_properties(record, sf_url, available_custom)

            if existing_page_id:
                if UPDATE:
                    if DRY_RUN:
                        print(f"  [dry-run] 更新: {name!r}")
                    else:
                        try:
                            await update_notion_page(existing_page_id, properties, client)
                            print(f"  ✅ 更新: {name!r}")
                            updated_count += 1
                        except Exception as e:
                            print(f"  ⚠️  更新失敗 ({name}): {e}")
                            skipped_count += 1
                else:
                    if DEBUG:
                        print(f"  [DEBUG] スキップ（既存）: {name!r}")
                    skipped_count += 1
            else:
                if DRY_RUN:
                    print(f"  [dry-run] 新規作成: {name!r}")
                else:
                    try:
                        await create_notion_page(properties, client)
                        print(f"  ✅ 新規作成: {name!r}")
                        created_count += 1
                    except Exception as e:
                        print(f"  ⚠️  作成失敗 ({name}): {e}")
                        skipped_count += 1

    # ③ 状態保存
    if not DRY_RUN:
        save_state(now.strftime("%Y-%m-%dT%H:%M:%SZ"), created_count + updated_count)

    ts_end = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"\n[{ts_end}] 完了: "
        f"新規作成={created_count}, 更新={updated_count}, スキップ={skipped_count}"
    )


if __name__ == "__main__":
    asyncio.run(main())
