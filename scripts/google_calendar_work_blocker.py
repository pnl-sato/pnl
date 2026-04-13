"""
Google カレンダー 作業ブロッカー

【概要】
Googleカレンダーに「Online:」を含む予定が作成・更新されたとき、
その前後30分に「作業」予定を自動で挿入する。

  例: "Online: 候補者Aさん面談" 14:00〜15:00
      → "作業" 13:30〜14:00  （前バッファ）
      → "作業" 15:00〜15:30  （後バッファ）

【セットアップ】
  1. Google Cloud Console でプロジェクトを作成
  2. Google Calendar API を有効化
  3. OAuth 2.0 クライアントID（デスクトップアプリ）を作成
  4. 認証情報を scripts/gcal_credentials.json として保存
  5. pip install -r requirements.txt
  6. python scripts/google_calendar_work_blocker.py --setup
     （ブラウザで認証 → scripts/gcal_token.json が自動生成）

【実行オプション】
  --setup     初回OAuth認証（ブラウザが開く）
  --dry-run   カレンダーを更新せず確認のみ
  --days N    何日先まで検索するか（デフォルト: 30）
  --debug     詳細ログ

【cron 例】5分おきに実行
  */5 * * * * cd /path/to/pnl && python scripts/google_calendar_work_blocker.py >> logs/gcal.log 2>&1

【環境変数】（.env に記載）
  GOOGLE_CALENDAR_ID  対象カレンダーID（省略時は "primary"）
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ─── 設定 ────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

CREDENTIALS_FILE = Path(__file__).parent / "gcal_credentials.json"
TOKEN_FILE       = Path(__file__).parent / "gcal_token.json"
STATE_FILE       = Path(__file__).parent / "gcal_state.json"

JST = ZoneInfo("Asia/Tokyo")

ONLINE_PREFIX  = "Online:"
WORK_TITLE     = "作業"
BUFFER_MINUTES = 30

DEBUG   = "--debug"   in sys.argv
DRY_RUN = "--dry-run" in sys.argv

# --days 引数の処理
LOOK_AHEAD_DAYS = 30
for i, arg in enumerate(sys.argv):
    if arg == "--days" and i + 1 < len(sys.argv):
        try:
            LOOK_AHEAD_DAYS = int(sys.argv[i + 1])
        except ValueError:
            pass
        break


# ─── OAuth 認証 ───────────────────────────────────────────────────────────────

def get_credentials() -> Credentials:
    """OAuth2認証情報を取得する。期限切れなら自動リフレッシュ、未認証ならブラウザ認証。"""
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"認証ファイルが見つかりません: {CREDENTIALS_FILE}\n"
                    "Google Cloud Console から OAuth クライアントID（デスクトップアプリ）を作成し、\n"
                    "credentials.json を scripts/gcal_credentials.json として保存してください。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json())

    return creds


# ─── 状態管理（処理済みイベントの追跡） ──────────────────────────────────────

def load_state() -> dict:
    """処理済みイベントの状態を読み込む。"""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"processed": {}}


def save_state(state: dict) -> None:
    """状態をファイルに保存する。"""
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def is_already_processed(state: dict, event_id: str, updated: str) -> bool:
    """このイベントの更新時刻まで処理済みか確認する。"""
    return state["processed"].get(event_id) == updated


def mark_processed(state: dict, event_id: str, updated: str) -> None:
    """イベントを処理済みとしてマークする。"""
    state["processed"][event_id] = updated


# ─── 時刻ユーティリティ ───────────────────────────────────────────────────────

def parse_event_datetime(time_obj: dict) -> datetime | None:
    """
    Google Calendar の time オブジェクトを datetime に変換する。
    終日イベント（date のみ）は None を返す。
    """
    if "dateTime" in time_obj:
        return datetime.fromisoformat(time_obj["dateTime"])
    return None  # 終日イベントはスキップ


def to_rfc3339(dt: datetime) -> str:
    """datetime を RFC3339 文字列に変換する。"""
    return dt.isoformat()


# ─── 作業ブロック操作 ─────────────────────────────────────────────────────────

def work_block_exists(service, calendar_id: str, target_start: datetime, target_end: datetime) -> bool:
    """
    指定した時間帯に「作業」イベントが既に存在するか確認する。
    開始・終了が完全一致するものを探す。
    """
    # 検索範囲を少し広げて取得
    results = service.events().list(
        calendarId=calendar_id,
        timeMin=to_rfc3339(target_start - timedelta(minutes=1)),
        timeMax=to_rfc3339(target_end   + timedelta(minutes=1)),
        q=WORK_TITLE,
        singleEvents=True,
    ).execute()

    for ev in results.get("items", []):
        ev_start = parse_event_datetime(ev.get("start", {}))
        ev_end   = parse_event_datetime(ev.get("end",   {}))
        if ev_start and ev_end:
            if ev_start == target_start and ev_end == target_end:
                return True

    return False


def create_work_block(
    service,
    calendar_id: str,
    start: datetime,
    end: datetime,
    related_title: str,
) -> None:
    """「作業」イベントをカレンダーに作成する。"""
    label = f"{start.strftime('%m/%d %H:%M')}〜{end.strftime('%H:%M')}"

    if DRY_RUN:
        print(f"   [dry-run] 作業ブロック作成: {label}")
        return

    service.events().insert(
        calendarId=calendar_id,
        body={
            "summary": WORK_TITLE,
            "description": f"「{related_title}」の前後作業時間",
            "start": {"dateTime": to_rfc3339(start), "timeZone": "Asia/Tokyo"},
            "end":   {"dateTime": to_rfc3339(end),   "timeZone": "Asia/Tokyo"},
        },
    ).execute()

    print(f"   ✅ 作業ブロック作成: {label}")


# ─── メイン処理 ───────────────────────────────────────────────────────────────

def process_online_events(service, calendar_id: str) -> None:
    """
    「Online:」を含む予定を今日〜N日先まで検索し、
    未処理のものに前後30分の「作業」ブロックを作成する。
    """
    now      = datetime.now(tz=JST)
    time_min = to_rfc3339(now)
    time_max = to_rfc3339(now + timedelta(days=LOOK_AHEAD_DAYS))

    state = load_state()

    print(f"\n🔍 「{ONLINE_PREFIX}」を含む予定を検索中（{LOOK_AHEAD_DAYS}日先まで）...")

    results = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        q=ONLINE_PREFIX,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    # q= は全文検索なので、タイトルに含まれるものだけに絞る
    all_items    = results.get("items", [])
    online_events = [ev for ev in all_items if ONLINE_PREFIX in ev.get("summary", "")]

    print(f"   {len(online_events)} 件の「{ONLINE_PREFIX}」予定を発見")

    added_count = 0
    skip_count  = 0

    for event in online_events:
        event_id = event["id"]
        updated  = event.get("updated", "")
        summary  = event.get("summary", "（タイトルなし）")

        start_dt = parse_event_datetime(event.get("start", {}))
        end_dt   = parse_event_datetime(event.get("end",   {}))

        if not start_dt or not end_dt:
            if DEBUG:
                print(f"   [DEBUG] 終日イベントはスキップ: {summary!r}")
            continue

        if DEBUG:
            print(
                f"\n   [DEBUG] 処理中: {summary!r} "
                f"({start_dt.strftime('%m/%d %H:%M')}〜{end_dt.strftime('%H:%M')})"
            )

        # 処理済みチェック：updated が同じなら変更なし → スキップ
        if is_already_processed(state, event_id, updated):
            skip_count += 1
            if DEBUG:
                print(f"   [DEBUG] スキップ（処理済み・変更なし）: {summary!r}")
            continue

        print(f"\n📅 {summary}")
        print(f"   {start_dt.strftime('%Y/%m/%d %H:%M')}〜{end_dt.strftime('%H:%M')}")

        pre_start  = start_dt - timedelta(minutes=BUFFER_MINUTES)
        pre_end    = start_dt
        post_start = end_dt
        post_end   = end_dt + timedelta(minutes=BUFFER_MINUTES)

        # 前バッファ
        if work_block_exists(service, calendar_id, pre_start, pre_end):
            print(f"   ⏭️  前作業（{pre_start.strftime('%H:%M')}〜{pre_end.strftime('%H:%M')}）: 既に存在")
        else:
            create_work_block(service, calendar_id, pre_start, pre_end, summary)
            added_count += 1

        # 後バッファ
        if work_block_exists(service, calendar_id, post_start, post_end):
            print(f"   ⏭️  後作業（{post_start.strftime('%H:%M')}〜{post_end.strftime('%H:%M')}）: 既に存在")
        else:
            create_work_block(service, calendar_id, post_start, post_end, summary)
            added_count += 1

        mark_processed(state, event_id, updated)

    if not DRY_RUN:
        save_state(state)

    print(
        f"\n📊 結果: {added_count} 件の作業ブロックを追加"
        + (f"、{skip_count} 件スキップ（変更なし）" if skip_count else "")
    )


def main() -> None:
    ts = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] Google カレンダー 作業ブロッカー 開始")

    if DRY_RUN:
        print("⚠️  --dry-run モード: カレンダーは更新しません")

    try:
        creds = get_credentials()
    except FileNotFoundError as e:
        print(f"❌ {e}")
        sys.exit(1)

    service     = build("calendar", "v3", credentials=creds)
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

    if DEBUG:
        print(f"   [DEBUG] カレンダーID: {calendar_id}")

    try:
        process_online_events(service, calendar_id)
    except Exception as e:
        print(f"❌ エラー: {e}")
        if DEBUG:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    ts_end = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts_end}] 完了")


if __name__ == "__main__":
    if "--setup" in sys.argv:
        print("=== Google Calendar OAuth 認証セットアップ ===")
        creds = get_credentials()
        print(f"✅ 認証完了。トークンを保存しました: {TOKEN_FILE}")
    else:
        main()
