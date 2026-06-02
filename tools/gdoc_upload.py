#!/usr/bin/env python3
"""テキストファイルを Google ドキュメントとして作成する（サービスアカウント認証）。

全文はディスクから読んで直接 Drive API に multipart upload するため、呼び出し側
（Claude）のコンテキストを通らない。text/plain を Google ドキュメントへ自動変換する。

依存: 標準ライブラリ + openssl コマンド（JWT の RS256 署名に使用）。

必要な環境変数:
    GOOGLE_SERVICE_ACCOUNT_JSON  サービスアカウント鍵。JSONファイルのパス or JSON文字列そのもの。
    GDRIVE_FOLDER_ID (任意)       作成先フォルダ/共有ドライブのID（SAに共有しておくこと）。
    GOOGLE_IMPERSONATE_SUBJECT (任意)  ドメイン全体委任で代理するユーザー（例 sato-y@pnl.co.jp）。

使い方:
    python3 tools/gdoc_upload.py <テキストファイル> <ドキュメントタイトル> [folder_id]
出力:
    作成したドキュメントの URL を標準出力に1行。
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import urllib.error

TOKEN_URI = "https://oauth2.googleapis.com/token"
UPLOAD_URL = ("https://www.googleapis.com/upload/drive/v3/files"
              "?uploadType=multipart&supportsAllDrives=true&fields=id,webViewLink")
SCOPE = "https://www.googleapis.com/auth/drive"


def load_sa():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        sys.exit("環境変数 GOOGLE_SERVICE_ACCOUNT_JSON が未設定です。")
    if os.path.isfile(raw):
        with open(raw, encoding="utf-8") as f:
            return json.load(f)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        sys.exit("GOOGLE_SERVICE_ACCOUNT_JSON はファイルパスか JSON 文字列を指定してください。")


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def sign_rs256(signing_input, private_key_pem):
    """openssl で RS256 署名（外部 crypto ライブラリ不要）。"""
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as kf:
        kf.write(private_key_pem)
        key_path = kf.name
    os.chmod(key_path, 0o600)
    try:
        p = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_path],
            input=signing_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if p.returncode != 0:
            sys.exit(f"[openssl 署名失敗] {p.stderr.decode(errors='replace')}")
        return p.stdout
    finally:
        os.unlink(key_path)


def get_access_token(sa):
    import time
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claim = {
        "iss": sa["client_email"],
        "scope": SCOPE,
        "aud": TOKEN_URI,
        "iat": now,
        "exp": now + 3600,
    }
    subject = os.environ.get("GOOGLE_IMPERSONATE_SUBJECT")
    if subject:
        claim["sub"] = subject
    signing_input = (b64url(json.dumps(header).encode()) + b"."
                     + b64url(json.dumps(claim).encode()))
    sig = sign_rs256(signing_input, sa["private_key"])
    assertion = (signing_input + b"." + b64url(sig)).decode()
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode()
    req = urllib.request.Request(TOKEN_URI, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        sys.exit(f"[token 取得失敗] HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")


def upload_doc(token, text_path, title, folder_id):
    with open(text_path, "rb") as f:
        content = f.read()
    metadata = {"name": title, "mimeType": "application/vnd.google-apps.document"}
    if folder_id:
        metadata["parents"] = [folder_id]

    boundary = "----pnl-gdoc-boundary"
    parts = [
        f"--{boundary}", "Content-Type: application/json; charset=UTF-8", "",
        json.dumps(metadata),
        f"--{boundary}", "Content-Type: text/plain; charset=UTF-8", "",
    ]
    body = ("\r\n".join(parts) + "\r\n").encode("utf-8") + content + \
           ("\r\n--" + boundary + "--\r\n").encode("utf-8")
    req = urllib.request.Request(
        UPLOAD_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": f"multipart/related; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"[drive upload 失敗] HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")
    return res.get("webViewLink") or f"https://docs.google.com/document/d/{res['id']}/edit"


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    text_path, title = sys.argv[1], sys.argv[2]
    folder_id = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("GDRIVE_FOLDER_ID")
    if not os.path.isfile(text_path):
        sys.exit(f"ファイルが見つかりません: {text_path}")
    sa = load_sa()
    token = get_access_token(sa)
    print(upload_doc(token, text_path, title, folder_id))


if __name__ == "__main__":
    main()
