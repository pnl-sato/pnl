"""
Gmail → Notion ToDo 同期スクリプト

【概要】
特定の Gmail ラベル（デフォルト: "NotionToDo"）が付いたメールを
Notion の ToDo DB にタスクとして書き出す。

【フィールドマッピング】
  メール件名       → ToDo.Title
  メール本文サマリ  → ToDo.説明（Claude API で生成、APIキーがない場合は先頭300文字）
  受信日時         → ToDo.開始時刻
  TaskType         → "Inbox 📨" 固定
  ステータス       → "未着手" 固定
  ページ本文       → 送信者・受信日時のメタ情報 ＋ メール本文全文

【セットアップ】
  1. Google Cloud Console でサービスアカウントを作成
       https://console.cloud.google.com/iam-admin/serviceaccounts
  2. 作成したサービスアカウントのキー（JSON）をダウンロード
  3. Gmail API を有効化
       https://console.cloud.google.com/apis/library/gmail.googleapis.com
  4. Google Workspace 管理コンソールでドメイン全体の委任を設定
       セキュリティ → API の制御 → ドメイン全体の委任 → 追加
       スコープ: https://www.googleapis.com/auth/gmail.modify
  5. Gmail で "NotionToDo" ラベルを作成し、処理したいメールに付ける
  6. .env に下記の設定を記入
  7. pip install -r requirements.txt

【.env 設定項目】
  NOTION_TOKEN                  Notion API トークン
  TODO_DB_ID                    Notion ToDo DB の ID（URLからコピーしてダッシュ除去）
  GMAIL_SERVICE_ACCOUNT_FILE    サービスアカウント JSON ファイルのパス
  GMAIL_USER_EMAIL              処理対象の Gmail アドレス
  GMAIL_LABEL                   対象ラベル名（デフォルト: NotionToDo）
  ANTHROPIC_API_KEY             （任意）サマリ生成用 Claude API キー

【実行オプション】
  --dry-run    Notion を更新せず確認のみ
  --debug      詳細ログを出力

【cron 例】15分ごと
  */15 * * * * cd /path/to/pnl && python scripts/gmail_notion_todo_sync.py >> logs/gmail_sync.log 2>&1
"""

import asyncio
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from email import message_from_bytes
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ─── 設定 ────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
TODO_DB_ID   = os.environ["TODO_DB_ID"]

GMAIL_SERVICE_ACCOUNT_FILE = os.environ["GMAIL_SERVICE_ACCOUNT_FILE"]
GMAIL_USER_EMAIL           = os.environ["GMAIL_USER_EMAIL"]
GMAIL_LABEL                = os.environ.get("GMAIL_LABEL", "NotionToDo")
GMAIL_DONE_LABEL           = os.environ.get("GMAIL_DONE_LABEL", f"{GMAIL_LABEL}/Done")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

STATE_FILE = Path(__file__).parent / "gmail_sync_state.json"

DEBUG   = "--debug"   in sys.argv
DRY_RUN = "--dry-run" in sys.argv

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


# ─── 処理済み状態管理 ──────────────────────────────────────────────────────────

def load_state() -> set[str]:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        return set(data.get("processed_ids", []))
    return set()


def save_state(processed_ids: set[str]) -> None:
    STATE_FILE.write_text(
        json.dumps({"processed_ids": sorted(processed_ids)}, ensure_ascii=False, indent=2)
    )


# ─── Gmail: サービス初期化 ─────────────────────────────────────────────────────

def build_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        GMAIL_SERVICE_ACCOUNT_FILE,
        scopes=GMAIL_SCOPES,
    ).with_subject(GMAIL_USER_EMAIL)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_label_id(service, label_name: str) -> str | None:
    """ラベル名からラベルIDを取得"""
    labels = service.users().labels().list(userId="me").execute()
    for label in labels.get("labels", []):
        if label["name"] == label_name:
            return label["id"]
    return None


def get_or_create_label(service, label_name: str) -> str:
    """ラベルを取得。なければ作成してIDを返す"""
    label_id = get_label_id(service, label_name)
    if label_id:
        return label_id

    created = service.users().labels().create(
        userId="me",
        body={
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    print(f"  📌 ラベルを作成しました: '{label_name}'")
    return created["id"]


# ─── Gmail: メール本文パース ───────────────────────────────────────────────────

def strip_html(html: str) -> str:
    """HTML からプレーンテキストを抽出"""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</?(p|div|tr)[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def get_email_body(msg) -> str:
    """メール本文を取得（プレーンテキスト優先、なければ HTML を変換）"""

    def decode_part(part) -> str:
        payload = part.get_payload(decode=True)
        if not payload:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")

    plain = ""
    html  = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not plain:
                plain = decode_part(part)
            elif ct == "text/html" and not html:
                html = decode_part(part)
    else:
        content = decode_part(msg)
        if msg.get_content_type() == "text/html":
            html = content
        else:
            plain = content

    if plain:
        return plain.strip()
    if html:
        return strip_html(html)
    return ""


# ─── Claude API: サマリ生成 ───────────────────────────────────────────────────

async def generate_summary(subject: str, body: str) -> str:
    """Claude API でメールのサマリを生成（APIキーがない場合は先頭300文字）"""
    if not ANTHROPIC_API_KEY:
        snippet = body[:300]
        return snippet + ("…" if len(body) > 300 else "")

    prompt = (
        "以下のメールを日本語で2〜3文に要約してください。要約のみ返してください。\n\n"
        f"件名: {subject}\n\n"
        f"本文:\n{body[:4000]}"
    )

    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        res.raise_for_status()
        return res.json()["content"][0]["text"].strip()


# ─── Notion: ページ作成 ───────────────────────────────────────────────────────

def _text_block(content: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": content}}]
        },
    }


def body_to_blocks(body: str, max_chars: int = 1900) -> list[dict]:
    """本文テキストを Notion paragraph ブロックのリストに変換"""
    blocks: list[dict] = []
    current = ""

    for line in body.splitlines():
        # 1行が max_chars を超える場合は強制分割
        while len(line) > max_chars:
            if current:
                blocks.append(_text_block(current))
                current = ""
            blocks.append(_text_block(line[:max_chars]))
            line = line[max_chars:]

        if len(current) + len(line) + 1 > max_chars:
            if current:
                blocks.append(_text_block(current.rstrip()))
            current = line + "\n"
        else:
            current += line + "\n"

    if current.strip():
        blocks.append(_text_block(current.strip()))

    return blocks or [_text_block("")]


async def create_notion_todo(
    subject: str,
    summary: str,
    received_at: datetime,
    sender: str,
    body: str,
) -> str:
    """Notion ToDo DB にページを作成してページ ID を返す"""

    meta_text = f"From: {sender}\nReceived: {received_at.strftime('%Y-%m-%d %H:%M %Z')}"
    header_blocks = [
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": meta_text}}],
                "icon": {"type": "emoji", "emoji": "📧"},
                "color": "gray_background",
            },
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]

    content_blocks = body_to_blocks(body)
    all_blocks     = (header_blocks + content_blocks)[:100]
    overflow       = (header_blocks + content_blocks)[100:]

    payload = {
        "parent": {"database_id": TODO_DB_ID},
        "properties": {
            "Title": {
                "title": [{"type": "text", "text": {"content": subject[:2000]}}]
            },
            "説明": {
                "rich_text": [{"type": "text", "text": {"content": summary[:2000]}}]
            },
            "開始時刻": {
                "date": {"start": received_at.isoformat()}
            },
            "ステータス": {
                "status": {"name": "未着手"}
            },
            "TaskType": {
                "select": {"name": "Inbox 📨"}
            },
        },
        "children": all_blocks,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            f"{NOTION_API}/pages",
            headers=NOTION_HEADERS,
            json=payload,
        )
        res.raise_for_status()
        page_id = res.json()["id"]

        # 100ブロックを超える場合は追記
        for i in range(0, len(overflow), 100):
            chunk = overflow[i : i + 100]
            append = await client.patch(
                f"{NOTION_API}/blocks/{page_id}/children",
                headers=NOTION_HEADERS,
                json={"children": chunk},
            )
            append.raise_for_status()

    return page_id


# ─── メイン: 1通のメールを処理 ────────────────────────────────────────────────

async def process_email(
    service,
    msg_id: str,
    done_label_id: str,
) -> bool:
    """1通のメールを処理して ToDo を作成。成功時 True を返す"""
    try:
        raw_msg = service.users().messages().get(
            userId="me", id=msg_id, format="raw"
        ).execute()

        raw_data = base64.urlsafe_b64decode(raw_msg["raw"] + "==")
        msg      = message_from_bytes(raw_data)

        subject  = msg.get("Subject", "(件名なし)")
        sender   = msg.get("From", "")
        date_str = msg.get("Date", "")

        try:
            received_at = parsedate_to_datetime(date_str)
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
        except Exception:
            received_at = datetime.now(timezone.utc)

        body = get_email_body(msg) or "(本文なし)"

        if DEBUG:
            print(f"  [DEBUG] 件名: {subject!r}")
            print(f"  [DEBUG] 送信者: {sender!r}")
            print(f"  [DEBUG] 本文 ({len(body)} 文字): {body[:100]!r}...")

        # サマリ生成
        print(f"  📝 サマリ生成中: {subject!r}")
        summary = await generate_summary(subject, body)
        print(f"     → {summary[:80]}{'…' if len(summary) > 80 else ''}")

        if DRY_RUN:
            print(f"  [dry-run] Notion ToDo 作成をスキップ")
            return True

        # Notion にページを作成
        page_id = await create_notion_todo(subject, summary, received_at, sender, body)
        print(f"  ✅ Notion ToDo 作成完了: {page_id}")

        # 処理済みラベルを付ける（元のラベルは維持）
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"addLabelIds": [done_label_id]},
        ).execute()

        return True

    except HttpError as e:
        print(f"  ❌ Gmail API エラー ({msg_id}): {e}")
        return False
    except Exception as e:
        print(f"  ❌ 処理失敗 ({msg_id}): {e}")
        if DEBUG:
            import traceback
            traceback.print_exc()
        return False


# ─── エントリーポイント ───────────────────────────────────────────────────────

async def main() -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] Gmail → Notion ToDo 同期開始")
    print(f"  対象ラベル: {GMAIL_LABEL!r} / 処理済みラベル: {GMAIL_DONE_LABEL!r}")

    if DRY_RUN:
        print("⚠️  --dry-run モード: Notion の更新は行いません")

    processed_ids = load_state()

    # Gmail サービス初期化
    try:
        service = build_gmail_service()
    except Exception as e:
        print(f"❌ Gmail サービスの初期化失敗: {e}")
        sys.exit(1)

    # ラベル ID を取得（対象ラベルがなければエラー、Doneラベルはなければ作成）
    source_label_id = get_label_id(service, GMAIL_LABEL)
    if not source_label_id:
        print(f"❌ Gmailラベル '{GMAIL_LABEL}' が見つかりません。")
        print(f"   Gmail の設定でラベルを作成してください。")
        sys.exit(1)

    done_label_id = get_or_create_label(service, GMAIL_DONE_LABEL)

    # 対象ラベルのメール一覧を取得（Done ラベルが付いていないものを絞り込む）
    print(f"\n📬 ラベル '{GMAIL_LABEL}' のメールを検索中...")
    try:
        result = service.users().messages().list(
            userId="me",
            labelIds=[source_label_id],
            maxResults=50,
        ).execute()
    except HttpError as e:
        print(f"❌ Gmail API エラー: {e}")
        sys.exit(1)

    messages = result.get("messages", [])
    if not messages:
        print("ℹ️  対象メールなし")
        return

    print(f"  {len(messages)} 件検出")

    new_count   = 0
    skip_count  = 0
    error_count = 0

    for msg_info in messages:
        msg_id = msg_info["id"]

        if msg_id in processed_ids:
            skip_count += 1
            if DEBUG:
                print(f"  [DEBUG] スキップ（処理済み）: {msg_id}")
            continue

        print(f"\n  処理中: {msg_id}")
        success = await process_email(service, msg_id, done_label_id)

        if success:
            processed_ids.add(msg_id)
            new_count += 1
        else:
            error_count += 1

    if not DRY_RUN:
        save_state(processed_ids)

    ts_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts_end}] 完了: {new_count} 件処理, {skip_count} 件スキップ, {error_count} 件エラー")


if __name__ == "__main__":
    asyncio.run(main())
