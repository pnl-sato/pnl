#!/usr/bin/env python3
"""Gemini で音声ファイルを文字起こしする最小スクリプト（標準ライブラリのみ）。

使い方:
    GEMINI_API_KEY=xxxx python3 tools/gemini_transcribe.py <音声ファイル> [プロンプト]

プロンプトの優先順位: 第2引数 > 環境変数 GEMINI_PROMPT > 既定プロンプト。
長文プロンプトは GEMINI_PROMPT で渡すとクォート不要で楽。
出力をファイルに逃がせば（> out.txt）全文を呼び出し側の文脈に載せずに済む。

仕様:
    - 20MB 未満は inline_data で直接送信、それ以上は Files API でアップロードしてから処理。
    - 既定モデルは gemini-2.5-flash（環境変数 GEMINI_MODEL で変更可）。
    - 出力は文字起こしテキストを標準出力へ。
"""
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.request
import urllib.error

API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
BASE = "https://generativelanguage.googleapis.com"
INLINE_LIMIT = 20 * 1024 * 1024  # 20MB
DEFAULT_PROMPT = (
    "この音声を日本語で正確に文字起こししてください。"
    "話者が複数いる場合は話者A/B等でラベル付けし、"
    "フィラー（えー、あの等）は適度に整理して読みやすくしてください。"
)


def _req(url, data=None, headers=None, method="GET"):
    headers = headers or {}
    body = json.dumps(data).encode() if isinstance(data, (dict, list)) else data
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def guess_mime(path):
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime
    ext = os.path.splitext(path)[1].lower()
    return {
        ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
        ".ogg": "audio/ogg", ".flac": "audio/flac", ".aac": "audio/aac",
    }.get(ext, "audio/mpeg")


def upload_via_files_api(path, mime, size):
    """Resumable upload (Files API) for files >= 20MB."""
    start_url = f"{BASE}/upload/v1beta/files?key={API_KEY}"
    headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(size),
        "X-Goog-Upload-Header-Content-Type": mime,
        "Content-Type": "application/json",
    }
    status, body, resp_headers = _req(
        start_url, data={"file": {"display_name": os.path.basename(path)}},
        headers=headers, method="POST",
    )
    if status != 200:
        sys.exit(f"[files api: start 失敗] {status} {body.decode(errors='replace')}")
    upload_url = resp_headers.get("X-Goog-Upload-URL") or resp_headers.get("x-goog-upload-url")
    if not upload_url:
        sys.exit(f"[files api] upload URL が取得できませんでした: {resp_headers}")

    with open(path, "rb") as f:
        raw = f.read()
    up_headers = {
        "Content-Length": str(size),
        "X-Goog-Upload-Offset": "0",
        "X-Goog-Upload-Command": "upload, finalize",
    }
    status, body, _ = _req(upload_url, data=raw, headers=up_headers, method="POST")
    if status != 200:
        sys.exit(f"[files api: upload 失敗] {status} {body.decode(errors='replace')}")
    file_info = json.loads(body)["file"]
    name, uri = file_info["name"], file_info["uri"]

    # ACTIVE になるまで待つ
    for _ in range(30):
        st, b, _ = _req(f"{BASE}/v1beta/{name}?key={API_KEY}")
        state = json.loads(b).get("state") if st == 200 else None
        if state == "ACTIVE":
            return uri, file_info["mimeType"]
        if state == "FAILED":
            sys.exit("[files api] 処理に失敗しました")
        time.sleep(2)
    sys.exit("[files api] ACTIVE になるまでタイムアウトしました")


def transcribe(path, prompt):
    size = os.path.getsize(path)
    mime = guess_mime(path)
    if size < INLINE_LIMIT:
        with open(path, "rb") as f:
            audio_part = {"inline_data": {"mime_type": mime,
                                          "data": base64.b64encode(f.read()).decode()}}
    else:
        uri, mime = upload_via_files_api(path, mime, size)
        audio_part = {"file_data": {"mime_type": mime, "file_uri": uri}}

    url = f"{BASE}/v1beta/models/{MODEL}:generateContent?key={API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}, audio_part]}]}
    status, body, _ = _req(url, data=payload,
                           headers={"Content-Type": "application/json"}, method="POST")
    if status != 200:
        sys.exit(f"[generateContent 失敗] {status} {body.decode(errors='replace')}")
    data = json.loads(body)
    try:
        return "".join(p.get("text", "")
                       for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        sys.exit(f"[応答の解析に失敗] {json.dumps(data, ensure_ascii=False)[:800]}")


def main():
    if not API_KEY:
        sys.exit("環境変数 GEMINI_API_KEY が未設定です。")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    if not os.path.isfile(path):
        sys.exit(f"ファイルが見つかりません: {path}")
    prompt = (sys.argv[2] if len(sys.argv) > 2
              else os.environ.get("GEMINI_PROMPT") or DEFAULT_PROMPT)
    print(transcribe(path, prompt))


if __name__ == "__main__":
    main()
