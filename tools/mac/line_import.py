#!/usr/bin/env python3
"""LINE の「トーク履歴を保存」で書き出した .txt を取り込み、候補者の履歴ページに追記する。

なぜ別スクリプトか:
    LINE はローカル DB が暗号化（Letter Sealing / E2EE）されており、個人アカウントの履歴を
    引き出す API も無い。iMessage の chat.db 直読みのような全自動はできない。唯一の正攻法は
    LINE（Mac/Win/スマホ）の「トーク履歴を保存／送信」で .txt を書き出すこと。本スクリプトは
    その .txt を解析し、iMessage/SMS と **同じ「メッセージ履歴」DB・同じ候補者ページ** の本文へ
    [LINE] タグ付きで時系列追記する（保存形式・色分けは imessage_sync.py と共通）。

    → Claude Code は候補者1ページで iMessage / SMS / LINE を横断して網羅的に読める。

前提（半自動運用）:
    - LINE で対象トークを開き「トーク履歴を保存」→ .txt を作る。古い分も欲しいときは保存前に
      上へスクロールして読み込ませる（LINE は表示済み分だけ書き出す仕様）。
    - 書き出した .txt を本スクリプトに渡す。重複は署名（日時＋送信者＋本文）で弾くので、
      前回と重なる範囲を含む .txt を渡しても二重追記されない。

使い方:
    NOTION_TOKEN=ntn_xxx python3 tools/mac/line_import.py <export.txt> [オプション]
      --candidate-id <page_id>   取り込み先の候補者ページを直接指定（最優先）
      --candidate "氏名"          候補者 DB の名前で照合して特定
      --self "佐藤 雄太"          自分の LINE 表示名（送受信判定用。未指定なら相手以外の送信者を自分と推定）
      --dry-run                  解析結果（件数・送受信内訳）だけ表示し Notion には書かない

候補者の自動特定:
    --candidate-id / --candidate が無ければ、.txt ヘッダ「[LINE] 〇〇とのトーク履歴」から相手名を
    取り、候補者 DB の名前（IMESSAGE_NAME_PROP）に照合する。一致しなければ中断するので明示指定する。

環境変数（imessage_sync.py と共通の .env を読む）:
    NOTION_TOKEN / IMESSAGE_CANDIDATE_DB_ID / IMESSAGE_MESSAGES_DB_ID / IMESSAGE_NAME_PROP
    LINE_SELF_NAME            自分の LINE 表示名（--self の既定値）
    LINE_WATERMARK_FILE       取り込み済み署名の保存先（既定 ~/.line_import_watermark.json）
"""
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imessage_sync as ims  # 同ディレクトリの共通ロジックを再利用


# ─────────────────────────────────────────── LINE .txt パーサ ──────────────

# 日付ヘッダ（複数フォーマットに対応）: 2026/06/09(火) / 2026.06.09 火曜日 / 英語表記
_DATE_PATTERNS = [
    re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})\(.\)\s*$"),
    re.compile(r"^(\d{4})[./](\d{1,2})[./](\d{1,2})(?:\s.*)?$"),
    re.compile(r"^\w{3},\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$"),  # Wed, 6/9/2026
]
# メッセージ行: HH:MM<TAB>送信者<TAB>本文
_MSG_RE = re.compile(r"^(\d{1,2}):(\d{2})\t(.*?)\t(.*)$")


def _parse_date_header(line):
    m = _DATE_PATTERNS[0].match(line) or _DATE_PATTERNS[1].match(line)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = _DATE_PATTERNS[2].match(line)  # 英語: mm/dd/yyyy
    if m:
        mo, d, y = m.group(1), m.group(2), m.group(3)
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def parse_line_export(path):
    """LINE 書き出し .txt をメッセージ list にする。
    返り値: (messages, partner_name)
      messages: [{date, hh, mm, sender, body}] を時系列で
      partner_name: ヘッダ「〇〇とのトーク履歴」から抽出した相手名（無ければ None）
    """
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")

    partner = None
    if lines:
        h = lines[0]
        m = (re.search(r"\[LINE\]\s*(.+?)とのトーク履歴", h)
             or re.search(r"\[LINE\]\s*Chat history with (.+)", h))
        if m:
            partner = m.group(1).strip()

    messages, cur_date, cur = [], None, None
    for raw in lines:
        line = raw.rstrip("\n")
        d = _parse_date_header(line)
        if d:
            cur_date = d
            cur = None
            continue
        m = _MSG_RE.match(line)
        if m and cur_date:
            hh, mm, sender, body = m.group(1), m.group(2), m.group(3), m.group(4)
            cur = {"date": cur_date, "hh": f"{int(hh):02d}", "mm": mm,
                   "sender": sender.strip(), "body": body}
            messages.append(cur)
        elif cur is not None and line != "":
            cur["body"] += "\n" + line  # 複数行メッセージの継続行
    return messages, partner


# ─────────────────────────────────────────── 候補者の特定 ──────────────────

def _norm_name(s):
    return re.sub(r"\s|　", "", s or "")


def candidate_name_map(db_id):
    name_prop = ims.cfg("IMESSAGE_NAME_PROP", "名前")
    out = {}
    for p in ims.notion_query_all(db_id):
        nm = ims._prop_value(p.get("properties", {}).get(name_prop, {}))
        if nm:
            out[_norm_name(nm)] = (p["id"], nm)
    return out


def resolve_candidate(args, partner, candidate_db):
    cid = ims._opt(args, "--candidate-id")
    if cid:
        return cid.replace("-", ""), (ims._opt(args, "--candidate") or partner or "(LINE)")
    name = ims._opt(args, "--candidate") or partner
    if not name:
        sys.exit("候補者を特定できません。--candidate-id か --candidate \"氏名\" を指定してください。")
    cmap = candidate_name_map(candidate_db)
    hit = cmap.get(_norm_name(name))
    if not hit:
        sys.exit(f"候補者 DB に「{name}」が見つかりません。--candidate-id で直接指定してください。")
    return hit[0], hit[1]


# ─────────────────────────────────────────── 重複防止（署名）──────────────

def watermark_path():
    return os.path.expanduser(ims.cfg("LINE_WATERMARK_FILE", "~/.line_import_watermark.json"))


def load_sigs():
    p = watermark_path()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_sigs(store):
    with open(watermark_path(), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)


def _sig(date, hh, mm, sender, body):
    raw = f"{date} {hh}:{mm}\t{sender}\t{body}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────────────────── main ──────────────────────────

def main():
    ims.load_dotenv()
    args = sys.argv[1:]
    paths = [a for a in args if not a.startswith("--") and a.lower().endswith(".txt")]
    if not paths:
        sys.exit(__doc__)
    txt_path = paths[0]
    if not os.path.exists(txt_path):
        sys.exit(f"ファイルが見つかりません: {txt_path}")

    candidate_db = ims.cfg("IMESSAGE_CANDIDATE_DB_ID")
    messages_db = ims.cfg("IMESSAGE_MESSAGES_DB_ID")
    if not candidate_db:
        sys.exit("IMESSAGE_CANDIDATE_DB_ID が未設定です（候補者 DB）。")

    parsed, partner = parse_line_export(txt_path)
    if not parsed:
        sys.exit("メッセージを1件も解析できませんでした（LINE のテキスト書き出し形式か確認してください）。")

    candidate_id, candidate_name = resolve_candidate(args, partner, candidate_db)

    # 自分の表示名（送受信判定）。未指定なら「相手名以外の送信者」を自分と推定。
    self_name = ims._opt(args, "--self") or ims.cfg("LINE_SELF_NAME")
    senders = {m["sender"] for m in parsed}
    if not self_name:
        others = senders - {partner} if partner else senders
        # 相手（partner）以外がちょうど1人ならそれを自分とみなす
        guess = senders - ({partner} if partner else set())
        if partner and len(guess) == 1:
            self_name = next(iter(guess))
        elif len(senders) == 2 and candidate_name:
            self_name = next((s for s in senders if _norm_name(s) != _norm_name(candidate_name)), None)
    # 重複署名で差分だけに絞る
    store = load_sigs()
    seen = set(store.get(candidate_id, []))
    new_msgs, new_sigs = [], []
    for m in parsed:
        sg = _sig(m["date"], m["hh"], m["mm"], m["sender"], m["body"])
        if sg in seen:
            continue
        seen.add(sg)
        new_sigs.append(sg)
        direction = "送信" if (self_name and _norm_name(m["sender"]) == _norm_name(self_name)) else "受信"
        iso = f"{m['date']}T{m['hh']}:{m['mm']}:00+09:00"
        new_msgs.append({
            "iso": iso,
            "ts": f"{m['date']} {m['hh']}:{m['mm']}",
            "body": m["body"],
            "direction": direction,
            "service": "LINE",
        })

    if "--dry-run" in args:
        sent = sum(1 for x in new_msgs if x["direction"] == "送信")
        print(f"[dry-run] {txt_path}")
        print(f"  相手(ヘッダ): {partner or '(不明)'} / 自分: {self_name or '(推定不可)'}")
        print(f"  取り込み先候補者: {candidate_name} ({candidate_id})")
        print(f"  解析 {len(parsed)} 件 / 新規 {len(new_msgs)} 件（送信 {sent} / 受信 {len(new_msgs) - sent}）"
              f" / 既存スキップ {len(parsed) - len(new_msgs)} 件")
        if not self_name:
            print("  ⚠ 自分の表示名を推定できませんでした。--self \"氏名\" を指定すると送受信が正確になります。")
        return

    if not new_msgs:
        print(f"✓ 新規メッセージなし（{candidate_name}）。すべて取り込み済みでした。")
        return
    if not messages_db:
        sys.exit("IMESSAGE_MESSAGES_DB_ID が未設定です（imessage_sync.py --setup で作成し .env に設定）。")
    if not self_name:
        sys.exit("自分の LINE 表示名を特定できません。--self \"氏名\" を指定して再実行してください。")

    new_msgs.sort(key=lambda x: x["iso"])
    page_id = ims.find_history_page(messages_db, candidate_id) \
        or ims.create_history_page(messages_db, candidate_id, candidate_name)
    ims.append_messages(page_id, new_msgs)
    store[candidate_id] = list(seen)
    save_sigs(store)
    sent = sum(1 for x in new_msgs if x["direction"] == "送信")
    print(f"✓ LINE 取り込み完了: {candidate_name} に新規 {len(new_msgs)} 件追記"
          f"（送信 {sent} / 受信 {len(new_msgs) - sent}）")


if __name__ == "__main__":
    main()
