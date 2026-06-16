#!/usr/bin/env python3
"""SF Opportunity（open・新規起票）を Gemini で構造化し Notion『案件サーチ｜SFミラー』へ upsert する。

なぜこれが要るか（案件サーチの recall 設計・agents/client-profile.md §7.5）:
    候補者への提案先サーチは、案件名キーワード一致＋壊れた Jobtype__c に頼ると
    取りこぼす（職種 null が約2割、誤タグ・重複・イベント告知の混在）。クエリ時に
    5,000件超を毎回読むのも非現実的。そこで「取り込み時に一度だけ構造化」する：
    各案件の JD（information__c）を Gemini に通し、Craft『完成版｜求人構造化評価
    プロンプト（候補者DB互換）』と同じタグ体系（会社フェーズ／役割タイプ／フェーズ適性／
    職種ラベル／想定職位／組織影響レンジ／支配変数／設計力・実行深度）へ正規化して
    Notion に置く。マッチ時はこの構造化済みDBを読むだけで recall を担保できる。

データの流れ（すべてスクリプト内で完結。Claude のコンテキストに JD 全文を通さない）:
    SF SOAP ログイン → REST SOQL（open × CreatedDate 直近N日）→ HTML 除去 →
    Gemini 構造化（JSON 強制）→ Notion upsert（SF案件ID で重複作成しない）

使い方:
    # 直近1日（夜間ルーティンの定常運用）
    python3 tools/sf_jobs_ingest.py
    # 初回バックフィル（直近30日）
    python3 tools/sf_jobs_ingest.py --days 30
    # 小さく試す（件数制限・書き込みあり）
    python3 tools/sf_jobs_ingest.py --days 1 --limit 5
    # 書き込まず分類結果だけ確認
    python3 tools/sf_jobs_ingest.py --days 1 --limit 3 --dry-run

必要な環境変数:
    SALESFORCE_USERNAME / SALESFORCE_PASSWORD / SALESFORCE_TOKEN / SALESFORCE_INSTANCE_URL
    GEMINI_API_KEY（任意 GEMINI_MODEL、既定 gemini-2.5-flash）
    NOTION_TOKEN（DB『案件サーチ｜SFミラー』にインテグレーションを共有しておくこと）
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

# --- Salesforce ---
SF_USER = os.environ.get("SALESFORCE_USERNAME")
SF_PASS = os.environ.get("SALESFORCE_PASSWORD")
SF_TOKEN = os.environ.get("SALESFORCE_TOKEN")
SF_LOGIN_URL = os.environ.get("SALESFORCE_INSTANCE_URL", "https://login.salesforce.com").rstrip("/")
SF_API_VER = "59.0"

# --- Gemini ---
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_BASE = "https://generativelanguage.googleapis.com"

# --- Notion ---
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
# DB『案件サーチ｜SFミラー』（Recruitment 配下。2026-06 作成）
JOBS_DATABASE_ID = "b17290c26d654e12adbac40331383a27"
KEY_PROP = "SF案件ID"  # upsert の主キー（= SF Opportunity Id）

# Craft『完成版｜求人構造化評価プロンプト（候補者DB互換）』の分類軸（正本は Craft）。
# enum を JD 横断で固定し、候補者DBと同じタグ体系に正規化する。
PHASE = ["Seed", "アーリー", "グロース", "IPO準備", "IPO後", "大企業", "再建/Turnaround"]
ARCHETYPE = ["Builder", "Scaler", "Operator", "Fixer", "Specialist"]
PHASE_FIT = ["0→1", "1→10", "10→100", "100→1000", "大企業運営", "再建/Turnaround"]
JOB_LABEL = ["事業企画", "COO/経営企画", "PdM", "セキュリティ", "エンジニアリング",
             "人事", "営業", "BizDev", "マーケティング", "コーポレート"]
SENIORITY = ["メンバー", "シニア", "マネージャー", "ディレクター", "部長", "役員", "CxO"]
ORG_IMPACT = ["個人", "〜10名", "10〜50名", "50〜200名", "200以上"]
KEY_VAR = ["売上", "コスト", "組織", "プロダクト", "ガバナンス", "データ", "オペレーション"]
POSTING_TYPE = ["実ポジション", "イベント・説明会", "重複・分割", "不明"]

GEMINI_INSTRUCTION = """あなたはエグゼクティブサーチの案件分析アシスタントです。
以下の求人情報（JD）を読み、候補者DB検索のための構造化ラベリングを JSON で返してください。

ルール:
- 表面的な文言ではなく、企業フェーズと組織状況から「実際に求められている役割」を推定する。
- 会社説明会・Meetup・イベント告知・登録専用ページなど、特定ポジションの募集でないものは posting_type を「イベント・説明会」にする。
- 年収は本文から読み取り万円単位の整数で返す（例:「500万円〜800万円」→ min 500, max 800）。
  記載が無ければ 0 を返す。
- dedup_key は「会社名｜役割の核」を正規化した短い文字列（重複・分割求人の名寄せ用。例「Macbee Planet｜クリエイティブディレクター」）。
- design_score（設計力）と execution_score（実行深度）は 0〜5 の整数。
- target_profile は「最もマッチしそうな人材像」を1〜2文で。
"""

GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "company_phase": {"type": "STRING", "enum": PHASE},
        "role_archetypes": {"type": "ARRAY", "items": {"type": "STRING", "enum": ARCHETYPE}},
        "phase_fit": {"type": "ARRAY", "items": {"type": "STRING", "enum": PHASE_FIT}},
        "job_labels": {"type": "ARRAY", "items": {"type": "STRING", "enum": JOB_LABEL}},
        "seniority": {"type": "STRING", "enum": SENIORITY},
        "org_impact": {"type": "STRING", "enum": ORG_IMPACT},
        "key_variables": {"type": "ARRAY", "items": {"type": "STRING", "enum": KEY_VAR}},
        "design_score": {"type": "INTEGER"},
        "execution_score": {"type": "INTEGER"},
        "target_profile": {"type": "STRING"},
        "salary_min_man": {"type": "INTEGER"},
        "salary_max_man": {"type": "INTEGER"},
        "posting_type": {"type": "STRING", "enum": POSTING_TYPE},
        "dedup_key": {"type": "STRING"},
        "summary": {"type": "STRING"},
    },
    "required": ["company_phase", "job_labels", "seniority", "posting_type",
                 "target_profile", "summary"],
}


# ---------------------------------------------------------------- HTTP helper
def _http(url, data=None, headers=None, method="GET", timeout=120):
    body = data
    if isinstance(data, (dict, list)):
        body = json.dumps(data).encode()
    elif isinstance(data, str):
        body = data.encode()
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ---------------------------------------------------------------- Salesforce
def sf_login():
    """SOAP ログインで sessionId と instance ベースURLを得る（simple_salesforce 不要）。"""
    pw = html.escape((SF_PASS or "") + (SF_TOKEN or ""))
    user = html.escape(SF_USER or "")
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:urn="urn:partner.soap.sforce.com"><soapenv:Body>'
        f'<urn:login><urn:username>{user}</urn:username>'
        f'<urn:password>{pw}</urn:password></urn:login>'
        '</soapenv:Body></soapenv:Envelope>'
    )
    url = f"{SF_LOGIN_URL}/services/Soap/u/{SF_API_VER}"
    status, body = _http(url, data=envelope, method="POST", headers={
        "Content-Type": "text/xml; charset=UTF-8", "SOAPAction": "login"})
    text = body.decode("utf-8", "replace")
    if status != 200:
        m = re.search(r"<faultstring>(.*?)</faultstring>", text, re.S)
        sys.exit(f"[SF login 失敗] HTTP {status}: {m.group(1) if m else text[:300]}")
    sid = re.search(r"<sessionId>(.*?)</sessionId>", text, re.S)
    surl = re.search(r"<serverUrl>(.*?)</serverUrl>", text, re.S)
    if not sid or not surl:
        sys.exit(f"[SF login 失敗] sessionId/serverUrl が取れません: {text[:300]}")
    instance = surl.group(1).split("/services/")[0]
    return sid.group(1), instance


def sf_query_all(session_id, instance, soql):
    """SOQL を全ページ走査して records を返す（nextRecordsUrl を辿る）。"""
    headers = {"Authorization": f"Bearer {session_id}"}
    url = f"{instance}/services/data/v{SF_API_VER}/query/?q={urllib.parse.quote(soql)}"
    out = []
    while url:
        status, body = _http(url, headers=headers)
        if status != 200:
            sys.exit(f"[SF query 失敗] HTTP {status}: {body.decode(errors='replace')[:300]}")
        data = json.loads(body)
        out.extend(data.get("records", []))
        nxt = data.get("nextRecordsUrl")
        url = f"{instance}{nxt}" if nxt else None
    return out


# ---------------------------------------------------------------- JD cleaning
def clean_jd(rec):
    raw = rec.get("information__c") or rec.get("Description") or ""
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</p>", "\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", "", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
    return raw[:16000]


# ---------------------------------------------------------------- Gemini
def gemini_classify(jd_text):
    payload = {
        "contents": [{"parts": [{"text": GEMINI_INSTRUCTION + "\n\n--- JD ---\n" + jd_text}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": GEMINI_SCHEMA,
            "temperature": 0,
            "maxOutputTokens": 4096,
            # gemini-2.5-* は thinking が既定ON で maxOutputTokens を食い、
            # JSON が途中で切れる。構造化抽出に推論は不要なので無効化する。
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = f"{GEMINI_BASE}/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    status, body = _http(url, data=payload, method="POST",
                         headers={"Content-Type": "application/json"}, timeout=120)
    if status != 200:
        raise RuntimeError(f"Gemini HTTP {status}: {body.decode(errors='replace')[:300]}")
    obj = json.loads(body)
    cands = obj.get("candidates", [])
    if not cands:
        raise RuntimeError(f"Gemini 応答に candidates なし: {json.dumps(obj)[:300]}")
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError("Gemini 応答が空")
    return json.loads(text)


# ---------------------------------------------------------------- Notion
def _nreq(path, payload=None, method="GET"):
    headers = {"Authorization": f"Bearer {NOTION_TOKEN}",
               "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}
    status, body = _http(f"{NOTION_API}{path}", data=payload, method=method, headers=headers)
    if status not in (200, 201):
        raise RuntimeError(f"Notion HTTP {status}: {body.decode(errors='replace')[:300]}")
    return json.loads(body)


def notion_find(sf_id):
    res = _nreq(f"/databases/{JOBS_DATABASE_ID}/query", method="POST", payload={
        "filter": {"property": KEY_PROP, "rich_text": {"equals": sf_id}}, "page_size": 1})
    rs = res.get("results", [])
    return rs[0]["id"] if rs else None


def _txt(s):
    return [{"text": {"content": (s or "")[:2000]}}]


def build_props(rec, c):
    name = rec.get("Name") or "(無題)"
    acct = (rec.get("Account") or {}).get("Name") or ""
    sf_id = rec["Id"]
    created = (rec.get("CreatedDate") or "")[:10]
    limited = rec.get("LimitedreleaseURL__c")
    category = "🟢即推薦可" if limited else "🟡新規打診"

    props = {
        "案件名": {"title": _txt(name)},
        KEY_PROP: {"rich_text": _txt(sf_id)},
        "会社名": {"rich_text": _txt(acct)},
        "カテゴリ": {"select": {"name": category}},
        "公開URL": {"url": rec.get("URL__c") or None},
        "限定公開URL": {"url": limited or None},
        "取込日": {"date": {"start": time.strftime("%Y-%m-%d")}},
    }
    if created:
        props["SF作成日"] = {"date": {"start": created}}

    if c is None:  # JD 無し or 分類失敗
        props["求人種別"] = {"select": {"name": "不明"}}
        return props

    def sel(field, val, allowed):
        if val in allowed:
            props[field] = {"select": {"name": val}}

    def msel(field, vals, allowed):
        clean = [v for v in (vals or []) if v in allowed]
        if clean:
            props[field] = {"multi_select": [{"name": v} for v in clean]}

    sel("会社フェーズ", c.get("company_phase"), PHASE)
    msel("役割タイプ", c.get("role_archetypes"), ARCHETYPE)
    msel("フェーズ適性", c.get("phase_fit"), PHASE_FIT)
    msel("職種ラベル", c.get("job_labels"), JOB_LABEL)
    sel("想定職位", c.get("seniority"), SENIORITY)
    sel("組織影響レンジ", c.get("org_impact"), ORG_IMPACT)
    msel("支配変数", c.get("key_variables"), KEY_VAR)
    sel("求人種別", c.get("posting_type"), POSTING_TYPE)
    for field, key in (("設計力", "design_score"), ("実行深度", "execution_score")):
        v = c.get(key)
        if isinstance(v, (int, float)):
            props[field] = {"number": v}
    for field, key in (("年収下限万円", "salary_min_man"), ("年収上限万円", "salary_max_man")):
        v = c.get(key)
        if isinstance(v, (int, float)) and v > 0:
            props[field] = {"number": v}
    if c.get("target_profile"):
        props["想定ターゲット人材"] = {"rich_text": _txt(c["target_profile"])}
    if c.get("dedup_key"):
        props["重複キー"] = {"rich_text": _txt(c["dedup_key"])}
    if c.get("summary"):
        props["要約"] = {"rich_text": _txt(c["summary"])}
    return props


def notion_upsert(rec, c):
    props = build_props(rec, c)
    page_id = notion_find(rec["Id"])
    if page_id:
        _nreq(f"/pages/{page_id}", method="PATCH", payload={"properties": props})
        return "updated"
    _nreq("/pages", method="POST",
          payload={"parent": {"database_id": JOBS_DATABASE_ID}, "properties": props})
    return "created"


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="SF open 案件を構造化して Notion へ upsert")
    ap.add_argument("--days", type=int, default=1, help="CreatedDate 直近N日（既定1）")
    ap.add_argument("--limit", type=int, default=0, help="処理件数の上限（0=無制限）")
    ap.add_argument("--dry-run", action="store_true", help="Notion に書かず結果を表示")
    ap.add_argument("--verbose", action="store_true", help="1件ごとに出力")
    args = ap.parse_args()

    for name, val in (("SALESFORCE_USERNAME", SF_USER), ("GEMINI_API_KEY", GEMINI_KEY)):
        if not val:
            sys.exit(f"環境変数 {name} が未設定です。")
    if not args.dry_run and not NOTION_TOKEN:
        sys.exit("環境変数 NOTION_TOKEN が未設定です（--dry-run なら不要）。")

    soql = (
        "SELECT Id, Name, information__c, Description, incomelimit__c, URL__c, "
        "LimitedreleaseURL__c, Jobtype__c, CreatedDate, Account.Name FROM Opportunity "
        f"WHERE StageName = 'open' AND CreatedDate = LAST_N_DAYS:{args.days} "
        "ORDER BY CreatedDate DESC"
    )
    sid, instance = sf_login()
    print(f"SF ログイン OK（{instance}）", file=sys.stderr)
    records = sf_query_all(sid, instance, soql)
    if args.limit:
        records = records[:args.limit]
    print(f"対象 {len(records)} 件（open × 直近{args.days}日）", file=sys.stderr)

    stats = {"created": 0, "updated": 0, "noJD": 0, "error": 0}
    for i, rec in enumerate(records, 1):
        name = rec.get("Name", "")[:50]
        try:
            jd = clean_jd(rec)
            c = None
            if len(jd) >= 40:
                c = gemini_classify(jd)
            else:
                stats["noJD"] += 1
            if args.dry_run:
                tag = (f"{c.get('posting_type')}/{c.get('job_labels')}/{c.get('seniority')}"
                       if c else "JD無し→不明")
                print(f"[{i}/{len(records)}] {name} :: {tag}")
            else:
                result = notion_upsert(rec, c)
                stats[result] += 1
                if args.verbose:
                    print(f"[{i}/{len(records)}] {result}: {name}")
            time.sleep(0.3)  # Gemini/Notion レート緩和
        except Exception as e:  # 1件の失敗で全体を止めない
            stats["error"] += 1
            print(f"[{i}/{len(records)}] ERROR {name}: {e}", file=sys.stderr)

    print(f"\n完了: created={stats['created']} updated={stats['updated']} "
          f"JD無し={stats['noJD']} error={stats['error']}", file=sys.stderr)


if __name__ == "__main__":
    main()
