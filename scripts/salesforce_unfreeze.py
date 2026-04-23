"""
Salesforce アカウント凍結解除スクリプト

【概要】
Salesforce 管理者の認証情報で SOAP ログインし、
凍結されているユーザーの IsFrozen フラグを REST API で false に更新する。

【セットアップ】
  cp .env.example .env
  # .env に SF_INSTANCE_URL / SF_USERNAME / SF_PASSWORD を記入

【実行例】
  python scripts/salesforce_unfreeze.py user@example.com

【環境変数】
  SF_INSTANCE_URL  例: https://poleline.my.salesforce.com
  SF_USERNAME      管理者ユーザー名 (例: admin@poleline.com)
  SF_PASSWORD      パスワードとセキュリティトークンを連結した文字列
                   例: MyPass1234aBcDeFgHiJkLmNoPqRsT
                   ※ セキュリティトークンは設定 → 個人情報 → セキュリティトークンのリセット で取得
"""

import asyncio
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

SF_INSTANCE_URL = os.environ["SF_INSTANCE_URL"].rstrip("/")
SF_USERNAME = os.environ["SF_USERNAME"]
SF_PASSWORD = os.environ["SF_PASSWORD"]  # password + security_token の連結

API_VERSION = "v59.0"


async def soap_login(client: httpx.AsyncClient) -> str:
    """SOAP API でログインしてセッション ID を返す"""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:urn="urn:partner.soap.sforce.com">'
        "<soapenv:Body><urn:login>"
        f"<urn:username>{SF_USERNAME}</urn:username>"
        f"<urn:password>{SF_PASSWORD}</urn:password>"
        "</urn:login></soapenv:Body>"
        "</soapenv:Envelope>"
    )
    res = await client.post(
        f"{SF_INSTANCE_URL}/services/Soap/u/59.0",
        content=body.encode(),
        headers={"Content-Type": "text/xml; charset=UTF-8", "SOAPAction": "login"},
    )
    res.raise_for_status()
    root = ET.fromstring(res.text)
    node = root.find(".//{urn:partner.soap.sforce.com}sessionId")
    if node is None:
        raise RuntimeError(f"ログイン失敗:\n{res.text[:500]}")
    return node.text


async def soql_query(client: httpx.AsyncClient, session_id: str, soql: str) -> list[dict]:
    res = await client.get(
        f"{SF_INSTANCE_URL}/services/data/{API_VERSION}/query/",
        params={"q": soql},
        headers={"Authorization": f"Bearer {session_id}"},
    )
    res.raise_for_status()
    return res.json().get("records", [])


async def patch_record(
    client: httpx.AsyncClient, session_id: str, sobject: str, record_id: str, payload: dict
) -> None:
    res = await client.patch(
        f"{SF_INSTANCE_URL}/services/data/{API_VERSION}/sobjects/{sobject}/{record_id}",
        json=payload,
        headers={"Authorization": f"Bearer {session_id}"},
    )
    if res.status_code not in (200, 204):
        raise RuntimeError(f"更新失敗: HTTP {res.status_code}\n{res.text}")


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print("使い方: python salesforce_unfreeze.py <username>")
        print("例:     python salesforce_unfreeze.py user@example.com")
        sys.exit(1)

    target_username = args[0]
    print(f"対象ユーザー: {target_username}")

    async with httpx.AsyncClient(timeout=30) as client:
        print("Salesforce にログイン中...")
        session_id = await soap_login(client)
        print("✅ ログイン成功")

        # ユーザー ID を取得
        users = await soql_query(
            client,
            session_id,
            f"SELECT Id, Username, IsActive FROM User WHERE Username = '{target_username}' LIMIT 1",
        )
        if not users:
            print(f"❌ ユーザーが見つかりません: {target_username}")
            sys.exit(1)

        user = users[0]
        user_id = user["Id"]
        print(f"   ユーザー ID : {user_id}")
        print(f"   IsActive    : {user['IsActive']}")

        # UserLogin レコードを取得
        ul_records = await soql_query(
            client,
            session_id,
            f"SELECT Id, IsFrozen FROM UserLogin WHERE UserId = '{user_id}' LIMIT 1",
        )
        if not ul_records:
            print("❌ UserLogin レコードが見つかりません")
            sys.exit(1)

        ul = ul_records[0]
        if not ul.get("IsFrozen"):
            print(f"ℹ️  '{target_username}' は既に凍結されていません。")
            return

        print(f"   UserLogin ID: {ul['Id']} (IsFrozen=True)")
        print("凍結解除中...")
        await patch_record(client, session_id, "UserLogin", ul["Id"], {"IsFrozen": False})
        print(f"✅ '{target_username}' の凍結を解除しました。")


if __name__ == "__main__":
    asyncio.run(main())
