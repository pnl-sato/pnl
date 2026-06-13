#!/usr/bin/env python3
"""候補者との iMessage / SMS 履歴だけを Mac の chat.db から抜き、Notion に保存する。

設計（CLAUDE.md セッション「mac-mini-message-history」での決定）:
    常時起動の mac mini 上の Claude Code（VSCode/CLI）からローカル実行する前提。
    許可リストの正本は **本来の「候補者」DB（Notion）** とし、その候補者ページに登録された
    携帯番号に一致するハンドルとのやりとり「だけ」を chat.db から抽出して、Notion の
    「メッセージ履歴 DB」に **候補者ページへの relation 付き** で upsert する。許可リスト外の
    番号（家族・私用）は構造上いっさい外に出ない（デフォルト拒否）。候補者ページから直接その人
    との iMessage/SMS 履歴を辿れるようになり、Web/スマホ版 Claude Code からも普段の Notion
    コネクタで読める。別建ての「番号マスター」は持たない（候補者情報の二重持ち・重複の温床になる）。

    - 読み取り専用に徹する（送信機能・書き込み機能は chat.db 側に一切持たせない）。
    - 標準ライブラリのみ（sqlite3 / urllib）。venv 不要、システム python3 で動く。
    - Notion へは REST API（NOTION_TOKEN インテグレーション）で直叩き（既存 tools/ と同じ流儀）。

⚠ 実行できるのは「その Mac 上で動く Claude Code（VSCode/CLI）」だけ。Web 版（クラウド）
   からは chat.db に触れないので読取・編集まで。詳細は tools/mac/README.md。

⚠ 事前準備（mini で1回ずつ・人手が要る）:
    1. Full Disk Access: Claude Code を動かすアプリ（ターミナル.app か VSCode）を
       システム設定 → プライバシーとセキュリティ → フルディスクアクセス に追加。
       これが無いと ~/Library/Messages/chat.db を開けない。
    2. SMS も取りたいなら iPhone の「設定 → メッセージ → テキストメッセージ転送」で mini をオン。
    3. Notion のインテグレーションに、候補者 DB とメッセージ履歴 DB の両方を共有する。

セットアップ（メッセージ履歴 DB をまだ作っていない場合）:
    # 親ページの page_id と候補者 DB の id を渡すと、候補者 DB に relation したメッセージ履歴 DB を作る
    NOTION_TOKEN=ntn_xxx python3 tools/mac/imessage_sync.py --setup \
        --parent <親ページの page_id> --candidate-db <候補者DBの id>
    # → 出力された database_id を tools/mac/.env の IMESSAGE_MESSAGES_DB_ID に設定する

日常運用:
    # 候補者 DB の各候補者ページに「携帯番号」を入れておく（許可リストの正本。これが一致条件）
    # 取り込みの下見（Notion には書かない。誰の何件が引けるかだけ表示）
    python3 tools/mac/imessage_sync.py --dry-run
    # 本番（差分のみ Notion に upsert）
    python3 tools/mac/imessage_sync.py
    # 初回だけ直近 N 日に絞る（巨大同期を避ける。既定は IMESSAGE_LOOKBACK_DAYS）
    python3 tools/mac/imessage_sync.py --lookback-days 90
    # 番号の表記ゆれ調整用: chat.db に出てくるハンドルと件数を覗く（本文は出さない）
    python3 tools/mac/imessage_sync.py --probe

環境変数（tools/mac/.env から自動読込）:
    NOTION_TOKEN              Notion インテグレーショントークン（必須）
    IMESSAGE_CANDIDATE_DB_ID  許可リストの正本＝候補者 DB の database_id（必須）
    IMESSAGE_MESSAGES_DB_ID   メッセージ履歴 DB の database_id（必須・--setup で作る）
    IMESSAGE_NAME_PROP        候補者 DB の氏名プロパティ名（既定 名前 / title）
    IMESSAGE_PHONE_PROP       候補者 DB の電話番号プロパティ名（既定 携帯番号 / phone_number）
    IMESSAGE_EMAIL_PROP       任意。iMessage の Apple ID メールを持つプロパティ名（既定 空＝未使用）
    IMESSAGE_DB_PATH          chat.db のパス（既定 ~/Library/Messages/chat.db）
    IMESSAGE_WATERMARK_FILE   差分同期の基準を保存する JSON（既定 ~/.imessage_sync_watermark.json）
    IMESSAGE_LOOKBACK_DAYS    初回（watermark 無し）の取得上限日数（既定 90、0 で無制限）
    IMESSAGE_DEFAULT_REGION   電話番号の既定国（既定 JP。先頭 0 → +81 変換などに使う）
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
JST = timezone(timedelta(hours=9), "JST")
APPLE_EPOCH = 978307200  # 2001-01-01 00:00:00 UTC を UNIX 時刻に直すオフセット

MAX_RICH_CHARS = 1900       # Notion rich_text は 2000 字/要素まで（余裕を見て 1900）
MAX_RICH_ITEMS = 90         # 1 プロパティの rich_text 配列は 100 要素まで


# ─────────────────────────────────────────── .env / 設定 ────────────────────

def load_dotenv():
    """スクリプトと同じディレクトリの .env を環境変数に流し込む（既存値は上書きしない）。
    python-dotenv に依存しないための最小実装。"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def cfg(name, default=None):
    return os.environ.get(name, default)


# ─────────────────────────────────────────── Notion REST ───────────────────

def _notion_req(path, payload=None, method="POST"):
    token = cfg("NOTION_TOKEN")
    if not token:
        sys.exit("環境変数 NOTION_TOKEN が未設定です。")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION,
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:600]
        sys.exit(f"[imessage_sync] Notion HTTP {e.code}: {body}")


def notion_query_all(db_id, payload=None):
    """database query を全ページング取得して results を返す。"""
    payload = dict(payload or {})
    payload.setdefault("page_size", 100)
    rows, cursor = [], None
    while True:
        if cursor:
            payload["start_cursor"] = cursor
        data = _notion_req(f"/databases/{db_id}/query", payload)
        rows.extend(data.get("results", []))
        if data.get("has_more"):
            cursor = data.get("next_cursor")
        else:
            break
    return rows


def _prop_value(prop):
    """Notion プロパティを素のテキストにする（title / rich_text / phone_number / email /
    url / formula(string) に対応）。許可リスト構築で型がまちまちなため吸収する。"""
    if not prop:
        return ""
    t = prop.get("type")
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in prop.get(t, [])).strip()
    if t == "phone_number":
        return (prop.get("phone_number") or "").strip()
    if t == "email":
        return (prop.get("email") or "").strip()
    if t == "url":
        return (prop.get("url") or "").strip()
    if t == "formula":
        return (prop.get("formula", {}).get("string") or "").strip()
    arr = prop.get("title") or prop.get("rich_text") or []
    return "".join(x.get("plain_text", "") for x in arr).strip()


# ─────────────────────────────────────────── 電話番号の正規化 ───────────────

def normalize_handle(raw):
    """chat.db のハンドル / 候補者 DB の番号を突き合わせ用キーに正規化する。

    返り値: (e164_or_email, suffix9)
      - email（Apple ID）はそのまま小文字化して返す（suffix は None）
      - 電話は E.164 風（+81... など）に寄せ、末尾9桁を suffix として併せて返す。
        chat.db は +81 付き / 先頭0 / 国番号なし が混在するため、末尾9桁での
        ゆるい一致もフォールバックに使う（日本の携帯の有意桁）。
    """
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s:
        return None, None
    if "@" in s:
        return s.lower(), None
    digits = re.sub(r"[^\d+]", "", s)
    region = (cfg("IMESSAGE_DEFAULT_REGION", "JP") or "JP").upper()
    cc = "81" if region == "JP" else None
    if digits.startswith("+"):
        e164 = digits
    elif digits.startswith("00") and len(digits) > 2:   # 国際プレフィックス
        e164 = "+" + digits[2:]
    elif digits.startswith("0") and cc:                  # 国内表記 → 国番号付与
        e164 = "+" + cc + digits[1:]
    elif cc and digits.startswith(cc):
        e164 = "+" + digits
    elif cc:
        e164 = "+" + cc + digits
    else:
        e164 = "+" + digits
    only = re.sub(r"\D", "", e164)
    suffix = only[-9:] if len(only) >= 9 else only
    return e164, suffix


def build_allowlist(candidate_db_id):
    """候補者 DB を読み、許可リストを作る。

    返り値:
      exact:  {e164_or_email: (page_id, name)}
      suffix: {末尾9桁: (page_id, name)}   ← フォールバック一致用
    氏名=NAME_PROP（既定「名前」/title）、電話=PHONE_PROP（既定「携帯番号」/phone_number）。
    EMAIL_PROP が設定されていれば Apple ID メールも許可リストに含める。電話/メールは
    カンマ・読点・改行区切りで複数可。携帯番号が空の候補者は許可リストに寄与しない。
    """
    name_prop = cfg("IMESSAGE_NAME_PROP", "名前")
    phone_prop = cfg("IMESSAGE_PHONE_PROP", "携帯番号")
    email_prop = cfg("IMESSAGE_EMAIL_PROP")  # 任意
    exact, suffix = {}, {}
    for page in notion_query_all(candidate_db_id):
        props = page.get("properties", {})
        pid = page["id"]
        name = _prop_value(props.get(name_prop, {})) or "(無名)"
        raw_vals = re.split(r"[,、\n\r/;]+", _prop_value(props.get(phone_prop, {})))
        if email_prop:
            raw_vals += re.split(r"[,、\n\r/;]+", _prop_value(props.get(email_prop, {})))
        for rv in raw_vals:
            key, suf = normalize_handle(rv)
            if not key:
                continue
            exact.setdefault(key, (pid, name))
            if suf:
                suffix.setdefault(suf, (pid, name))
    return exact, suffix


def match_handle(handle_id, exact, suffix):
    """chat.db のハンドル文字列を許可リストに当て、(page_id, name) か None を返す。"""
    key, suf = normalize_handle(handle_id)
    if key and key in exact:
        return exact[key]
    if key and key in suffix:        # email はここには来ない（suf=None）
        return suffix[key]
    if suf and suf in suffix:
        return suffix[suf]
    return None


# ─────────────────────────────────────────── chat.db 読み取り ──────────────

def open_chat_db_readonly():
    """chat.db を WAL ごと一時コピーして読み取り専用で開く。
    元ファイルには一切書き込まない（コピーを開く）。"""
    src = os.path.expanduser(cfg("IMESSAGE_DB_PATH", "~/Library/Messages/chat.db"))
    if not os.path.exists(src):
        sys.exit(
            f"chat.db が見つかりません: {src}\n"
            "  - Mac 上で実行していますか？（このスクリプトはローカル Mac 専用）\n"
            "  - 実行アプリ（ターミナル/VSCode）にフルディスクアクセスを付与しましたか？"
        )
    tmpdir = tempfile.mkdtemp(prefix="imessage_sync_")
    dst = os.path.join(tmpdir, "chat.db")
    for ext in ("", "-wal", "-shm"):
        s = src + ext
        if os.path.exists(s):
            shutil.copy2(s, dst + ext)
    conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn, tmpdir


def decode_attributed_body(blob):
    """attributedBody（streamtyped バイナリ）から本文テキストを取り出す。

    Ventura 以降は本文が message.text ではなく attributedBody（NSAttributedString を
    archive したバイナリ）に入ることが多く、素の SQL では空に見える。これは広く使われて
    いるヒューリスティック復号で、NSString マーカー直後の長さ付き文字列を拾う。完全な
    typedstream パーサではないため、稀に失敗したら None を返す（その行は本文空扱い）。"""
    if not blob:
        return None
    try:
        data = bytes(blob)
        if b"NSString" not in data:
            return None
        data = data.split(b"NSString", 1)[1]
        data = data[5:]  # クラスチャンクのプリアンブルを読み飛ばす
        if not data:
            return None
        if data[0] == 0x81:                       # 0x81 = 続く2バイトが長さ（LE）
            length = int.from_bytes(data[1:3], "little")
            start = 3
        else:
            length = data[0]
            start = 1
        text = data[start:start + length]
        return text.decode("utf-8", errors="replace") or None
    except Exception:
        return None


def apple_time_to_iso(value):
    """message.date（2001 基準）を JST ISO8601 に変換。ns / 秒 の両方に対応。"""
    if not value:
        return None
    unix = value / 1e9 + APPLE_EPOCH if value > 1e11 else value + APPLE_EPOCH
    return datetime.fromtimestamp(unix, JST).isoformat()


def fetch_messages(conn, matched_handle_rows, after_rowid, since_unix):
    """許可リストに一致した handle ROWID 群について、差分メッセージを ROWID 昇順で返す。

    1:1 スレッド前提で message.handle_id を使う（グループチャットは対象外。候補者との
    個別やりとりに絞る方針）。after_rowid より大きい行、かつ since_unix 以降に限定。
    """
    if not matched_handle_rows:
        return []
    ids = list(matched_handle_rows.keys())
    placeholders = ",".join("?" for _ in ids)
    sql = (
        "SELECT m.ROWID AS rowid, m.guid AS guid, m.date AS date, m.text AS text, "
        "       m.attributedBody AS abody, m.is_from_me AS is_from_me, "
        "       m.service AS service, m.handle_id AS handle_id "
        "FROM message m "
        f"WHERE m.handle_id IN ({placeholders}) AND m.ROWID > ? "
        "ORDER BY m.ROWID ASC"
    )
    params = ids + [after_rowid]
    out = []
    since_apple = None
    if since_unix:
        since_apple = int((since_unix - APPLE_EPOCH) * 1e9)  # ns 基準で比較
    for r in conn.execute(sql, params):
        if since_apple is not None and r["date"] and r["date"] < since_apple:
            continue
        body = (r["text"] or "").strip() or decode_attributed_body(r["abody"])
        if not body:
            continue  # 添付のみ・リアクション等は本文が無いのでスキップ
        pid, name = matched_handle_rows[r["handle_id"]]
        out.append({
            "rowid": r["rowid"],
            "guid": r["guid"],
            "iso": apple_time_to_iso(r["date"]),
            "body": body,
            "direction": "送信" if r["is_from_me"] else "受信",
            "service": r["service"] or "iMessage",
            "candidate_id": pid,
            "candidate_name": name,
        })
    return out


def matched_handles(conn, exact, suffix):
    """handle テーブルを走査し、許可リストに一致する {handle ROWID: (page_id, name)} を返す。"""
    matched = {}
    for r in conn.execute("SELECT ROWID AS rowid, id AS id FROM handle"):
        hit = match_handle(r["id"], exact, suffix)
        if hit:
            matched[r["rowid"]] = hit
    return matched


# ─────────────────────────────────────────── watermark ─────────────────────

def watermark_path():
    return os.path.expanduser(cfg("IMESSAGE_WATERMARK_FILE", "~/.imessage_sync_watermark.json"))


def load_watermark():
    p = watermark_path()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return int(json.load(f).get("last_rowid", 0))
        except Exception:
            return 0
    return 0


def save_watermark(rowid):
    with open(watermark_path(), "w", encoding="utf-8") as f:
        json.dump({"last_rowid": int(rowid),
                   "updated": datetime.now(JST).isoformat()}, f, ensure_ascii=False)


# ─────────────────────────────────────────── Notion 書き込み ───────────────

def _rich(text):
    """本文を 2000 字制限内の rich_text 配列に分割。"""
    items, t = [], text
    while t and len(items) < MAX_RICH_ITEMS:
        items.append({"type": "text", "text": {"content": t[:MAX_RICH_CHARS]}})
        t = t[MAX_RICH_CHARS:]
    return items


def guid_exists(messages_db_id, guid):
    res = _notion_req(f"/databases/{messages_db_id}/query",
                      {"filter": {"property": "GUID", "rich_text": {"equals": guid}},
                       "page_size": 1})
    return bool(res.get("results"))


def create_message_page(messages_db_id, msg):
    props = {
        "相手": {"title": [{"text": {"content": msg["candidate_name"]}}]},
        "日時": {"date": {"start": msg["iso"]}} if msg["iso"] else {"date": None},
        "方向": {"select": {"name": msg["direction"]}},
        "サービス": {"select": {"name": msg["service"]}},
        "本文": {"rich_text": _rich(msg["body"])},
        "GUID": {"rich_text": [{"text": {"content": msg["guid"]}}]},
        "候補者": {"relation": [{"id": msg["candidate_id"]}]},
    }
    _notion_req("/pages", {"parent": {"database_id": messages_db_id}, "properties": props})


# ─────────────────────────────────────────── --setup（DB 作成）─────────────

def setup_messages_db(parent_page_id, candidate_db_id):
    """候補者 DB に relation したメッセージ履歴 DB を1つ作る。"""
    parent_page_id = parent_page_id.replace("-", "")
    candidate_db_id = candidate_db_id.replace("-", "")
    messages = _notion_req("/databases", {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "メッセージ履歴｜iMessage/SMS"}}],
        "properties": {
            "相手": {"title": {}},
            "日時": {"date": {}},
            "方向": {"select": {"options": [
                {"name": "送信", "color": "blue"}, {"name": "受信", "color": "green"}]}},
            "サービス": {"select": {"options": [
                {"name": "iMessage", "color": "blue"}, {"name": "SMS", "color": "gray"}]}},
            "本文": {"rich_text": {}},
            "GUID": {"rich_text": {}},
            "候補者": {"relation": {"database_id": candidate_db_id, "single_property": {}}},
        },
    })
    print("✓ メッセージ履歴 DB を作成しました。tools/mac/.env に以下を設定してください：")
    print(f"  IMESSAGE_MESSAGES_DB_ID={messages['id'].replace('-', '')}")
    print(f"  IMESSAGE_CANDIDATE_DB_ID={candidate_db_id}")
    print("※ インテグレーションに候補者 DB とこの DB が共有されているか確認してください。")


# ─────────────────────────────────────────── --probe（番号調整用）──────────

def probe(conn, exact, suffix, limit=40):
    """chat.db に出てくるハンドルと件数を一覧表示（本文は出さない）。
    番号の表記ゆれ調整・許可リスト漏れの確認用。"""
    rows = conn.execute(
        "SELECT h.id AS id, COUNT(*) AS n FROM message m "
        "JOIN handle h ON m.handle_id = h.ROWID "
        "GROUP BY h.id ORDER BY n DESC"
    ).fetchall()
    print(f"chat.db のハンドル（上位{limit}件 / 全{len(rows)}件）  ✓=候補者DBの携帯番号と一致")
    for r in rows[:limit]:
        hit = match_handle(r["id"], exact, suffix)
        mark = "✓" if hit else " "
        who = f"  → {hit[1]}" if hit else ""
        print(f"  [{mark}] {r['n']:>5}件  {r['id']}{who}")
    hit = sum(1 for r in rows if match_handle(r["id"], exact, suffix))
    print(f"一致ハンドル: {hit} / {len(rows)}")


# ─────────────────────────────────────────── main ──────────────────────────

def _opt(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default


def main():
    load_dotenv()
    args = sys.argv[1:]

    candidate_db = cfg("IMESSAGE_CANDIDATE_DB_ID")

    if "--setup" in args:
        parent = _opt(args, "--parent") or cfg("IMESSAGE_SETUP_PARENT_PAGE_ID")
        cand = _opt(args, "--candidate-db") or candidate_db
        if not parent or not cand:
            sys.exit("--setup には --parent <page_id> と --candidate-db <候補者DBの id>"
                     "（または IMESSAGE_CANDIDATE_DB_ID）が必要です。")
        setup_messages_db(parent, cand)
        return

    messages_db = cfg("IMESSAGE_MESSAGES_DB_ID")
    if not candidate_db:
        sys.exit("IMESSAGE_CANDIDATE_DB_ID が未設定です（許可リストの正本＝候補者 DB）。")

    exact, suffix = build_allowlist(candidate_db)
    if not exact:
        print("⚠ 候補者 DB に携帯番号が登録された候補者が1人もいません。")
    conn, tmpdir = open_chat_db_readonly()
    try:
        if "--probe" in args:
            probe(conn, exact, suffix)
            return

        matched = matched_handles(conn, exact, suffix)
        last_rowid = load_watermark()
        # 初回（watermark 無し）は lookback で巨大同期を避ける
        lookback = int(_opt(args, "--lookback-days", cfg("IMESSAGE_LOOKBACK_DAYS", "90")))
        since_unix = None
        if last_rowid == 0 and lookback > 0:
            since_unix = (datetime.now(JST) - timedelta(days=lookback)).timestamp()

        msgs = fetch_messages(conn, matched, last_rowid, since_unix)
        dry = "--dry-run" in args

        if dry:
            from collections import Counter
            by_name = Counter(m["candidate_name"] for m in msgs)
            print(f"[dry-run] 一致ハンドル {len(matched)} 種 / 取り込み対象 {len(msgs)} 件"
                  + (f"（直近{lookback}日に限定）" if since_unix else ""))
            for name, n in by_name.most_common():
                print(f"  {n:>5}件  {name}")
            if msgs:
                print(f"  watermark は現在 {last_rowid} → 本番実行で {msgs[-1]['rowid']} まで進みます")
            return

        if not messages_db:
            sys.exit("IMESSAGE_MESSAGES_DB_ID が未設定です（--setup で作成し .env に設定）。")
        created = skipped = 0
        max_rowid = last_rowid
        for m in msgs:
            max_rowid = max(max_rowid, m["rowid"])
            if guid_exists(messages_db, m["guid"]):
                skipped += 1
                continue
            create_message_page(messages_db, m)
            created += 1
        if max_rowid > last_rowid:
            save_watermark(max_rowid)
        print(f"✓ 同期完了: 新規 {created} 件 / 既存スキップ {skipped} 件 "
              f"/ watermark {last_rowid}→{max_rowid}")
    finally:
        conn.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
