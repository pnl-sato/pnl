#!/usr/bin/env python3
"""
動画自動処理スクリプト

Blackholeで録画した .mov ファイルを監視し、以下を自動実行：
1. ffmpeg で圧縮 mp4 + m4a 音声ファイルを生成
2. YouTube アップロード（mp4 アーカイブ）と Gemini 文字起こし（txt 出力）を並列実行
3. m4a・txt を出力フォルダへ配置（NotebookLM アップロード用）

watch モードのフロー:
  1. 新規 .mov を検知 → macOS 通知 + ターミナルにメッセージ表示
  2. Finder でファイル名を整える（例: 20240327_山田太郎_候補者面談.mov）
     ※ このファイル名が YouTube タイトルにそのまま使われる
  3. Enter を押すと処理開始
     - ffmpeg 変換（mp4 + m4a）
     - [並列] YouTube アップロード ＋ Gemini 文字起こし → txt 保存

必要な準備:
  brew install ffmpeg
  pip install -r requirements.txt
  .env に以下を設定:
    YOUTUBE_CLIENT_SECRETS_FILE  （初回のみブラウザ認証が走る）
    GEMINI_API_KEY               （https://aistudio.google.com/app/apikey）

使い方:
  # フォルダ監視（録画フォルダを指定）
  python video_processor.py watch ~/Movies/Recordings

  # 単体処理（ファイル名を整えてから実行）
  python video_processor.py process ~/Movies/20240327_山田太郎_候補者面談.mov

  # 文字起こしのみ（YouTube スキップ）
  python video_processor.py --no-youtube process ~/Movies/rec.mov
"""

from __future__ import annotations

import argparse
import json
import shutil
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── 設定 ────────────────────────────────────────────────────────────────────

# 出力先フォルダ（デフォルト: 録画ファイルと同じ場所）
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "")

# YouTube 認証ファイルのパス（Google Cloud Console でダウンロードした JSON）
YOUTUBE_CLIENT_SECRETS = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")

# YouTube トークンキャッシュ
YOUTUBE_TOKEN_FILE = os.getenv("YOUTUBE_TOKEN_FILE", "~/.youtube_token.json")

# YouTube 動画の公開設定（"private" / "unlisted" / "public"）
YOUTUBE_PRIVACY = os.getenv("YOUTUBE_PRIVACY", "private")

# mp4 圧縮品質（CRF: 小さいほど高画質。18〜28 が一般的。デフォルト 23）
VIDEO_CRF = os.getenv("VIDEO_CRF", "23")

# m4a 音声ビットレート（NotebookLM の 100MB 制限に収まるよう設定）
# 面談・通話の文字起こし用途では 64k モノラルで品質十分
# 64k mono: ~29 MB/時間 → 3時間まで余裕あり（128k stereo だと2時間超で制限超過）
AUDIO_BITRATE = os.getenv("AUDIO_BITRATE", "64k")

# 処理済みファイルの記録（二重処理防止）
PROCESSED_LOG = os.getenv("PROCESSED_LOG", "~/.video_processor_done.json")

# Gemini API キー（文字起こし用）
# https://aistudio.google.com/app/apikey で取得
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# 文字起こしに使用する Gemini モデル
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# モデルが廃止・利用不可の場合に順番に試すフォールバックリスト
_GEMINI_FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

# 文字起こしプロンプトファイルのパス（単一ファイル指定）
# 未指定またはファイルが存在しない場合はデフォルトプロンプトを使用
TRANSCRIPT_PROMPT_FILE = os.getenv("TRANSCRIPT_PROMPT_FILE", "")

# 面談カテゴリ別プロンプトを格納するディレクトリ
# ここに「候補者面談.txt」「打ち合わせ.txt」などを置くと
# 処理開始前にカテゴリ選択メニューが表示される
# TRANSCRIPT_PROMPT_FILE より優先される（選択した場合）
TRANSCRIPT_PROMPT_DIR = os.getenv("TRANSCRIPT_PROMPT_DIR", "")

# 議事録生成プロンプトを格納するディレクトリ（カテゴリ別 .txt）
# 文字起こし完了後に Gemini で議事録を生成し Notion に追記する
MINUTES_PROMPT_DIR = os.getenv("MINUTES_PROMPT_DIR", "")

# Notion 連携（議事録自動追記）
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_MEMO_DB_ID = os.getenv("NOTION_MEMO_DB_ID", "")

# P&L 担当者名（文字起こしの話者ラベルに使用）
# .env に PNL_INTERVIEWER_NAME=佐藤 のように設定する
PNL_INTERVIEWER_NAME = os.getenv("PNL_INTERVIEWER_NAME", "")

# Google Drive 連携（文字起こしを Google Doc としてアップロード）
# 未設定の場合は .txt のみ保存
DRIVE_TOKEN_FILE = os.getenv("DRIVE_TOKEN_FILE", "~/.drive_token.json")
DRIVE_TRANSCRIPT_FOLDER_ID = os.getenv("DRIVE_TRANSCRIPT_FOLDER_ID", "")


# ─── ffmpeg 処理 ──────────────────────────────────────────────────────────────

def run_ffmpeg(args: list[str]) -> None:
    """ffmpeg コマンドを実行し、失敗時は例外を投げる。"""
    # launchd 経由の起動では PATH が限られるため明示的に解決する
    ffmpeg_bin = (
        shutil.which("ffmpeg")
        or "/opt/homebrew/bin/ffmpeg"
        or "/usr/local/bin/ffmpeg"
    )
    cmd = [ffmpeg_bin, "-y"] + args
    log.info("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("ffmpeg stderr:\n%s", result.stderr[-2000:])
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode})")


def convert_mov_to_mp4_and_m4a(mov_path: Path, output_dir: Path) -> tuple[Path, Path]:
    """
    .mov → 圧縮 .mp4 と .m4a を生成する。

    mp4: H.264 / CRF圧縮（画質を保ちながらサイズ削減）
    m4a: AAC 音声のみ（NotebookLM / AI文字起こし用）

    Returns:
        (mp4_path, m4a_path)
    """
    stem = mov_path.stem
    mp4_path = output_dir / f"{stem}.mp4"
    m4a_path = output_dir / f"{stem}.m4a"

    log.info("変換開始: %s", mov_path.name)

    # mp4 変換（映像 + 音声）
    run_ffmpeg([
        "-i", str(mov_path),
        "-c:v", "libx264",
        "-crf", VIDEO_CRF,
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "128k",
        str(mp4_path),
    ])
    log.info("mp4 生成完了: %s (%.1f MB)", mp4_path.name, mp4_path.stat().st_size / 1e6)

    # m4a 抽出（音声のみ・モノラル）
    # NotebookLM の 100MB 制限対策: モノラル + 64k = 約 29 MB/時間
    # 面談・通話の文字起こし用途ではステレオ不要、品質に影響なし
    run_ffmpeg([
        "-i", str(mov_path),
        "-vn",
        "-ac", "1",          # モノラルに変換（ファイルサイズを半減）
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        str(m4a_path),
    ])
    log.info("m4a 生成完了: %s (%.1f MB)", m4a_path.name, m4a_path.stat().st_size / 1e6)

    return mp4_path, m4a_path


# ─── YouTube アップロード ─────────────────────────────────────────────────────

def _build_youtube_service():
    """YouTube Data API v3 サービスオブジェクトを返す。初回はブラウザ認証が走る。"""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise ImportError(
            "YouTube API ライブラリが見つかりません。\n"
            "pip install google-api-python-client google-auth-oauthlib を実行してください。"
        )

    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    token_path = Path(YOUTUBE_TOKEN_FILE).expanduser()
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            secrets_path = Path(YOUTUBE_CLIENT_SECRETS).expanduser()
            if not secrets_path.exists():
                raise FileNotFoundError(
                    f"YouTube クライアントシークレットが見つかりません: {secrets_path}\n"
                    "Google Cloud Console からダウンロードして .env の "
                    "YOUTUBE_CLIENT_SECRETS_FILE に設定してください。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), scopes)
            creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_to_youtube(video_path: Path, title: str, description: str = "") -> str:
    """
    動画ファイルを YouTube にアップロードし、動画 ID を返す。

    元の .mov をそのままアップロードすることで再エンコードによる画質劣化を防ぐ。
    Broken pipe 等の一時障害は指数バックオフで最大10回リトライする。

    Returns:
        YouTube 動画 ID（例: "dQw4w9WgXcQ"）
    """
    from googleapiclient.http import MediaFileUpload

    youtube = _build_youtube_service()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "22",  # People & Blogs
        },
        "status": {
            "privacyStatus": YOUTUBE_PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }

    mime = "video/quicktime" if video_path.suffix.lower() == ".mov" else "video/mp4"
    media = MediaFileUpload(str(video_path), mimetype=mime, resumable=True)

    log.info("YouTube アップロード開始: %s (%s)", title, video_path.name)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    consecutive_errors = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            consecutive_errors = 0
            if status:
                pct = int(status.progress() * 100)
                log.info("アップロード進捗: %d%%", pct)
        except Exception as e:
            consecutive_errors += 1
            if consecutive_errors > 10:
                raise
            wait = min(2 ** consecutive_errors, 60)
            log.warning("アップロード一時エラー（%d回目）: %s → %d秒後リトライ", consecutive_errors, e, wait)
            time.sleep(wait)

    video_id = response["id"]
    log.info("YouTube アップロード完了: https://youtu.be/%s", video_id)
    return video_id


# ─── 処理済み管理 ─────────────────────────────────────────────────────────────

def _load_processed() -> dict:
    path = Path(PROCESSED_LOG).expanduser()
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_processed(data: dict) -> None:
    path = Path(PROCESSED_LOG).expanduser()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def is_processed(mov_path: Path) -> bool:
    entry = _load_processed().get(str(mov_path.resolve()))
    if entry is None:
        return False
    # 旧フォーマット（mp4_done フィールドなし）は完了済みとして扱う
    if "mp4_done" not in entry:
        return True
    return entry.get("completed_at") is not None


def _load_state(mov_path: Path) -> dict | None:
    """ファイルの処理状態を返す。未記録・旧フォーマットの場合は None。"""
    entry = _load_processed().get(str(mov_path.resolve()))
    if entry is None or "mp4_done" not in entry:
        return None
    return entry


def _save_state(mov_path: Path, state: dict) -> None:
    """処理状態をファイルに保存する。"""
    data = _load_processed()
    data[str(mov_path.resolve())] = state
    _save_processed(data)


# ─── Gemini 文字起こし ────────────────────────────────────────────────────────

# デフォルトの文字起こしプロンプト
# TRANSCRIPT_PROMPT_FILE でテキストファイルを指定するとそちらが優先される
_DEFAULT_TRANSCRIPT_PROMPT = """\
この音声ファイルを文字起こししてください。

ルール:
- 話者が複数いる場合は「【話者A】」「【話者B】」のように区別してください
- 固有名詞（人名・企業名・サービス名）はそのまま記載してください
- フィラー（「えー」「あのー」など）は省略して構いません
- 聞き取れなかった箇所は「（聞き取り不可）」と記載してください
- 文字起こし以外の説明文・コメントは不要です。本文のみ出力してください
"""


def _select_transcript_prompt() -> tuple[str | None, str | None]:
    """
    TRANSCRIPT_PROMPT_DIR にある .txt ファイルをカテゴリ一覧として表示し、
    ユーザーに選択させる。(プロンプト本文, カテゴリ名) のタプルを返す。

    - ディレクトリ未設定 / 空 / .txt ファイルなし の場合は (None, None) を返す
    - 「s. スキップ」でデフォルトプロンプトにフォールバック
    """
    if not TRANSCRIPT_PROMPT_DIR:
        return None, None

    prompt_dir = Path(TRANSCRIPT_PROMPT_DIR).expanduser()
    if not prompt_dir.exists():
        log.warning("TRANSCRIPT_PROMPT_DIR が見つかりません: %s", prompt_dir)
        return None, None

    prompt_files = sorted(prompt_dir.glob("*.txt"))
    if not prompt_files:
        log.warning("TRANSCRIPT_PROMPT_DIR にプロンプトファイルがありません: %s", prompt_dir)
        return None, None

    if sys.platform == "darwin":
        # macOS: ダイアログで選択
        names = [f.stem for f in prompt_files]
        chosen = _osascript_choose_from_list(
            title="P&L 動画処理",
            prompt="面談カテゴリを選択してください:",
            items=names,
        )
        if chosen is None:
            log.info("カテゴリ選択スキップ → デフォルトプロンプトを使用")
            return None, None
        selected = prompt_files[names.index(chosen)]
    else:
        # 非 macOS: ターミナル入力
        print("\n面談カテゴリを選択してください:")
        for i, f in enumerate(prompt_files, 1):
            print(f"  {i}. {f.stem}")
        print("  s. スキップ（デフォルトプロンプト）")
        while True:
            try:
                raw = input("番号を入力: ").strip().lower()
                if raw == "s":
                    return None, None
                choice = int(raw)
                if 1 <= choice <= len(prompt_files):
                    selected = prompt_files[choice - 1]
                    break
                print(f"1〜{len(prompt_files)} または s を入力してください")
            except (ValueError, EOFError):
                pass
            except KeyboardInterrupt:
                return None, None

    content = selected.read_text(encoding="utf-8").strip()
    log.info("プロンプト選択: %s", selected.name)
    return content, selected.stem


def _load_minutes_prompt(category_name: str | None) -> str | None:
    """議事録生成プロンプトをカテゴリ名から読み込む。"""
    if not MINUTES_PROMPT_DIR or not category_name:
        return None
    prompt_path = Path(MINUTES_PROMPT_DIR).expanduser() / f"{category_name}.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8").strip()
    log.warning("議事録プロンプトが見つかりません: %s", prompt_path)
    return None


_CHUNK_DURATION_SEC = 20 * 60  # 20分ごとにチャンク分割

_AUDIO_MIME_BY_EXT: dict[str, str] = {
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}

# 監視・処理対象の拡張子（動画 + 音声）
_WATCHED_SUFFIXES: frozenset[str] = frozenset({".mov", ".mp3", ".m4a", ".wav", ".ogg", ".flac"})

# 音声のみのファイル（ffmpeg変換・YouTubeアップロードをスキップ）
_AUDIO_ONLY_SUFFIXES: frozenset[str] = _WATCHED_SUFFIXES - {".mov"}


def _get_audio_duration(path: Path) -> float:
    """ffprobeで音声の長さ（秒）を返す。失敗時は0。"""
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _split_audio_chunks(path: Path, chunk_sec: int) -> list[Path]:
    """音声ファイルをchunk_sec秒ごとにチャンク分割し、パスのリストを返す。"""
    ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    tmp_dir = path.parent / f"_chunks_{path.stem}"
    tmp_dir.mkdir(exist_ok=True)
    out_pattern = str(tmp_dir / f"chunk_%03d{path.suffix}")
    subprocess.run(
        [ffmpeg_bin, "-y", "-i", str(path),
         "-f", "segment", "-segment_time", str(chunk_sec),
         "-c", "copy", "-reset_timestamps", "1", out_pattern],
        check=True, capture_output=True,
    )
    chunks = sorted(tmp_dir.glob(f"chunk_*{path.suffix}"))
    log.info("チャンク分割完了: %d チャンク", len(chunks))
    return chunks


def _transcribe_chunk(chunk_path: Path, client, types, prompt: str, audio_mime: str, max_retries: int = 5) -> str:
    """1チャンクをGeminiにアップロードして文字起こし結果を返す。失敗時は新セッションでリトライ。"""
    last_exc: Exception = RuntimeError("unknown")
    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            wait = min(2 ** attempt, 60)
            log.warning("チャンクリトライ %d/%d: %d秒後", attempt, max_retries, wait)
            time.sleep(wait)
        try:
            log.info("チャンクアップロード: %s (試行 %d)", chunk_path.name, attempt)
            with open(chunk_path, "rb") as f:
                audio_file = client.files.upload(
                    file=f,
                    config=types.UploadFileConfig(mime_type=audio_mime),
                )
            while audio_file.state.name == "PROCESSING":
                time.sleep(5)
                audio_file = client.files.get(name=audio_file.name)
            if audio_file.state.name != "ACTIVE":
                raise RuntimeError(f"Gemini チャンク処理失敗: {audio_file.state.name}")

            response, _ = _generate_with_fallback(
                client, GEMINI_MODEL,
                contents=[types.Content(parts=[
                    types.Part(file_data=types.FileData(file_uri=audio_file.uri, mime_type=audio_mime)),
                    types.Part(text=prompt),
                ])],
            )
            try:
                client.files.delete(name=audio_file.name)
            except Exception:
                pass
            return _strip_cjk_inner_spaces(response.text.strip())
        except Exception as e:
            last_exc = e
            log.warning("チャンクアップロード失敗: %s", e)
    raise last_exc


def _strip_cjk_inner_spaces(text: str) -> str:
    """CJK文字間の不要な半角スペースを除去する（Gemini分かち書き対策）。"""
    import re
    return re.sub(r'(?<=[　-鿿＀-￯]) (?=[　-鿿＀-￯])', '', text)


def generate_minutes_with_gemini(transcript_text: str, prompt: str) -> str:
    """文字起こしテキストからGeminiで議事録を生成する。"""
    from google import genai
    from google.genai import types as _types
    client = genai.Client(api_key=GEMINI_API_KEY, http_options=_types.HttpOptions(timeout=300_000))
    full_prompt = f"{prompt}\n\n---\n\n{transcript_text}"
    response, _ = _generate_with_fallback(client, GEMINI_MODEL, contents=full_prompt)
    return response.text


def find_notion_page_by_pdf_name(pdf_name: str) -> str | None:
    """PDFNameプロパティでNotionの面談メモDBを検索してpage_idを返す。"""
    import httpx
    url = f"https://api.notion.com/v1/databases/{NOTION_MEMO_DB_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    payload = {
        "filter": {
            "property": "PDFName",
            "formula": {"string": {"equals": pdf_name}},
        }
    }
    response = httpx.post(url, headers=headers, json=payload)
    response.raise_for_status()
    pages = response.json().get("results", [])
    if pages:
        return pages[0]["id"]
    log.warning("NotionページがPDFName '%s' で見つかりませんでした", pdf_name)
    return None


def append_minutes_to_notion(page_id: str, minutes_text: str) -> None:
    """Notionページの本文末尾に議事録テキストを追記する。"""
    import re
    import httpx

    def _parse_rich_text(text: str) -> list[dict]:
        """**bold** をNotionのrich_text形式に変換する。"""
        parts = re.split(r"(\*\*.*?\*\*)", text)
        result = []
        for part in parts:
            if part.startswith("**") and part.endswith("**"):
                result.append({
                    "type": "text",
                    "text": {"content": part[2:-2][:2000]},
                    "annotations": {"bold": True},
                })
            elif part:
                result.append({"type": "text", "text": {"content": part[:2000]}})
        return result or [{"type": "text", "text": {"content": ""}}]

    def _to_blocks(text: str) -> list[dict]:
        """MarkdownテキストをNotionブロックのリストに変換する。"""
        blocks = []
        for line in text.splitlines():
            s = line.rstrip()
            if s.startswith("# "):
                blocks.append({"object": "block", "type": "heading_1",
                                "heading_1": {"rich_text": _parse_rich_text(s[2:])}})
            elif s.startswith("## "):
                blocks.append({"object": "block", "type": "heading_2",
                                "heading_2": {"rich_text": _parse_rich_text(s[3:])}})
            elif s.startswith("### "):
                blocks.append({"object": "block", "type": "heading_3",
                                "heading_3": {"rich_text": _parse_rich_text(s[4:])}})
            elif s.startswith("- ") or s.startswith("* "):
                blocks.append({"object": "block", "type": "bulleted_list_item",
                                "bulleted_list_item": {"rich_text": _parse_rich_text(s[2:])}})
            elif re.match(r"^\d+\. ", s):
                content = re.sub(r"^\d+\. ", "", s)
                blocks.append({"object": "block", "type": "numbered_list_item",
                                "numbered_list_item": {"rich_text": _parse_rich_text(content)}})
            elif s in ("---", "***", "___"):
                blocks.append({"object": "block", "type": "divider", "divider": {}})
            elif s == "":
                pass  # 空行はスキップ
            else:
                blocks.append({"object": "block", "type": "paragraph",
                                "paragraph": {"rich_text": _parse_rich_text(s)}})
        return blocks

    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    blocks = _to_blocks(minutes_text)
    # Notion APIは一度に100ブロックまで
    for i in range(0, len(blocks), 100):
        response = httpx.patch(url, headers=headers, json={"children": blocks[i : i + 100]})
        response.raise_for_status()
    log.info("Notion追記完了: page_id=%s (%d blocks)", page_id, len(blocks))


def _notify_macos(title: str, message: str) -> None:
    """macOS デスクトップ通知を送る。"""
    if sys.platform == "darwin":
        subprocess.run(
            ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
            capture_output=True, check=False,
        )


def _generate_with_fallback(client, primary_model: str, **kwargs):
    """モデルが廃止・利用不可の場合フォールバックモデルを順番に試す。"""
    try:
        from google.genai.errors import ClientError
    except ImportError:
        return client.models.generate_content(model=primary_model, **kwargs), primary_model

    models_to_try = [primary_model] + [m for m in _GEMINI_FALLBACK_MODELS if m != primary_model]
    last_exc: Exception = RuntimeError("モデルリストが空です")
    for model in models_to_try:
        try:
            response = client.models.generate_content(model=model, **kwargs)
            if model != primary_model:
                log.warning("フォールバックモデルを使用しました: %s", model)
            return response, model
        except ClientError as e:
            if getattr(e, "status_code", None) == 404:
                log.warning("モデル利用不可 (%s)、次を試します...", model)
                last_exc = e
                continue
            raise
    raise last_exc


def _build_drive_service():
    """Google Drive APIサービスを構築する。認証情報がなければNoneを返す。"""
    token_path = Path(DRIVE_TOKEN_FILE).expanduser()
    if not token_path.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        creds = Credentials.from_authorized_user_file(str(token_path), ["https://www.googleapis.com/auth/drive.file"])
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        log.warning("Google Drive サービス構築失敗: %s", e)
        return None


def upload_text_as_gdoc(service, title: str, html_bytes: bytes, folder_id: str = "") -> str | None:
    """HTMLをGoogle Docとしてアップロードし、ドキュメントURLを返す。"""
    try:
        import io
        from googleapiclient.http import MediaIoBaseUpload
        metadata = {"name": title, "mimeType": "application/vnd.google-apps.document"}
        if folder_id:
            metadata["parents"] = [folder_id]
        media = MediaIoBaseUpload(io.BytesIO(html_bytes), mimetype="text/html", resumable=False)
        f = service.files().create(body=metadata, media_body=media, fields="id").execute()
        doc_id = f.get("id")
        url = f"https://docs.google.com/document/d/{doc_id}"
        log.info("Google Doc 作成完了: %s", url)
        if sys.platform == "darwin":
            subprocess.run(["open", url], check=False)
        return url
    except Exception as e:
        log.warning("Google Doc アップロード失敗: %s", e)
        return None


def _load_transcript_prompt() -> str:
    """
    文字起こしプロンプトを返す（非インタラクティブフォールバック）。

    TRANSCRIPT_PROMPT_FILE が設定されていてファイルが存在すればその内容を使う。
    それ以外はデフォルトプロンプトを返す。
    """
    if TRANSCRIPT_PROMPT_FILE:
        prompt_path = Path(TRANSCRIPT_PROMPT_FILE).expanduser()
        if prompt_path.exists():
            prompt = prompt_path.read_text(encoding="utf-8").strip()
            log.info("カスタムプロンプトを使用: %s", prompt_path)
            return prompt
        else:
            log.warning(
                "TRANSCRIPT_PROMPT_FILE が見つかりません: %s（デフォルトを使用）", prompt_path
            )
    return _DEFAULT_TRANSCRIPT_PROMPT


def transcribe_with_gemini(
    m4a_path: Path, out_dir: Path, prompt_override: str | None = None,
    category_name: str | None = None,
) -> Path:
    """
    Gemini API で m4a を文字起こしし、テキストファイルに保存する。

    Args:
        m4a_path: 文字起こし対象の音声ファイル
        out_dir:  出力先ディレクトリ

    Returns:
        保存したテキストファイルのパス
    """
    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY が設定されていません。\n"
            "https://aistudio.google.com/app/apikey で取得し .env に設定してください。"
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ImportError(
            "google-genai が未インストールです。\n"
            "pip install google-genai を実行してください。"
        )

    client = genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(timeout=600_000))
    txt_path = out_dir / f"文字起こし：{m4a_path.stem}.txt"

    log.info("Gemini 文字起こし開始: %s", m4a_path.name)

    # ファイルを Gemini File API にアップロード
    # Path オブジェクトを渡すと SDK がファイル名を内部で使い日本語エラーになるため
    # バイナリで開いて渡す
    audio_mime = _AUDIO_MIME_BY_EXT.get(m4a_path.suffix.lower(), "audio/mp4")

    # 文字起こしプロンプトを構築
    prompt = prompt_override if prompt_override is not None else _load_transcript_prompt()
    _CANDIDATE_CATEGORIES = {"候補者面談"}
    _candidate_name = m4a_path.stem.split("-")[0].strip() if "-" in m4a_path.stem else ""
    if (category_name in _CANDIDATE_CATEGORIES and _candidate_name and PNL_INTERVIEWER_NAME):
        prompt = (
            f"話者は2名います。"
            f"P&L（エージェント）側の話者ラベルは「【P&L {PNL_INTERVIEWER_NAME}】」、"
            f"候補者側の話者ラベルは「【{_candidate_name}】」としてください。\n\n"
        ) + prompt

    # 長い音声はチャンク分割してアップロード
    duration = _get_audio_duration(m4a_path)
    chunks: list[Path] = []
    chunk_results: list[str] = []
    try:
        if duration > _CHUNK_DURATION_SEC:
            log.info("音声が長いためチャンク分割します（%.0f分 → %d分×N）", duration / 60, _CHUNK_DURATION_SEC // 60)
            chunks = _split_audio_chunks(m4a_path, _CHUNK_DURATION_SEC)
        else:
            chunks = [m4a_path]

        for i, chunk in enumerate(chunks, 1):
            log.info("文字起こし %d/%d: %s", i, len(chunks), chunk.name)
            chunk_results.append(_transcribe_chunk(chunk, client, types, prompt, audio_mime))
    finally:
        # 一時チャンクディレクトリを削除
        tmp_dir = m4a_path.parent / f"_chunks_{m4a_path.stem}"
        if tmp_dir.exists():
            import shutil as _shutil
            _shutil.rmtree(tmp_dir, ignore_errors=True)

    used_model = GEMINI_MODEL
    transcript = "\n\n".join(chunk_results)

    # ヘッダー + 本文を .txt として保存
    header = "\n".join([
        "# 文字起こし",
        f"# ファイル : {m4a_path.name}",
        f"# 作成日時 : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"# モデル  : {used_model}",
        "─" * 60,
        "",
    ])
    txt_content = header + "\n" + transcript
    txt_path.write_text(txt_content, encoding="utf-8")
    log.info("文字起こし完了: %s", txt_path.name)

    # Google Drive に HTML 形式でアップロード（任意）
    drive_service = _build_drive_service()
    if drive_service:
        import html as _html
        body_html = "\n".join(
            f'<p style="margin:0">{_html.escape(line)}</p>' if line.strip() else '<p style="margin:0">&nbsp;</p>'
            for line in txt_content.splitlines()
        )
        html_bytes = f'<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>{body_html}</body></html>'.encode("utf-8")
        upload_text_as_gdoc(drive_service, txt_path.stem, html_bytes, DRIVE_TRANSCRIPT_FOLDER_ID)

    return txt_path


# ─── タイトル生成 ─────────────────────────────────────────────────────────────

def build_youtube_title(mov_path: Path) -> str:
    """
    ファイル名をそのまま YouTube タイトルに使う。

    watch モードでユーザーが整えたファイル名（例: 20240327_山田太郎_候補者面談.mov）が
    そのままタイトルになる。日付がファイル名に含まれていれば末尾に付与しない。

    例:
      "20240327_山田太郎_候補者面談"   → "[録画] 20240327_山田太郎_候補者面談"
      "Screen Recording 2024-03-27"   → "[録画] Screen Recording 2024-03-27"
    """
    return f"[録画] {mov_path.stem}"


# ─── メイン処理 ───────────────────────────────────────────────────────────────

def process_file(
    mov_path: Path,
    skip_youtube: bool = False,
    skip_transcribe: bool = False,
    prompt_override: str | None = None,
    minutes_prompt: str | None = None,
    category_name: str | None = None,
) -> None:
    """
    1つの .mov ファイルを処理する。

    各ステップ完了後に状態を保存するチェックポイント方式を採用。
    ネットワーク遮断等で中断しても、次回起動時に未完了ステップから再開できる。
    """
    mov_path = mov_path.resolve()

    if not mov_path.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {mov_path}")

    if is_processed(mov_path):
        log.info("処理済みのためスキップ: %s", mov_path.name)
        return

    # 出力先を決定
    if OUTPUT_DIR:
        out_dir = Path(OUTPUT_DIR).expanduser()
    else:
        out_dir = mov_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    is_audio_only = mov_path.suffix.lower() in _AUDIO_ONLY_SUFFIXES

    # 既存の状態を読み込む（再開時）または初期状態を作成
    state = _load_state(mov_path)
    if state is None:
        state = {
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "category_name": category_name,
            "skip_youtube": skip_youtube or is_audio_only,
            "skip_transcribe": skip_transcribe,
            "mp4_path": "",
            "mp4_done": is_audio_only,   # 音声ファイルはffmpeg不要
            "m4a_path": str(mov_path) if is_audio_only else "",
            "m4a_done": is_audio_only,
            "youtube_id": "",
            "youtube_done": is_audio_only,  # 音声ファイルはYouTube不要
            "transcript_path": "",
            "transcript_done": False,
            "notion_done": False,
        }
        _save_state(mov_path, state)
    else:
        log.info("前回の続きから再開します: %s", mov_path.name)
        is_audio_only = state.get("m4a_path") == str(mov_path)

    # 状態から変数を復元
    mp4_path = Path(state["mp4_path"]) if state.get("mp4_path") else None
    m4a_path = Path(state["m4a_path"]) if state.get("m4a_path") else None
    transcript_path = Path(state["transcript_path"]) if state.get("transcript_path") else None
    youtube_id = state.get("youtube_id", "")

    # 1. ffmpeg 変換（mp4 + m4a）
    if not state.get("mp4_done") or not state.get("m4a_done"):
        mp4_path, m4a_path = convert_mov_to_mp4_and_m4a(mov_path, out_dir)
        state.update({
            "mp4_path": str(mp4_path), "mp4_done": True,
            "m4a_path": str(m4a_path), "m4a_done": True,
        })
        _save_state(mov_path, state)
    else:
        log.info("ffmpeg変換済みのためスキップ: %s", mov_path.stem)

    # 2. Gemini 文字起こし
    if not skip_transcribe and GEMINI_API_KEY and not state.get("transcript_done"):
        try:
            transcript_path = transcribe_with_gemini(m4a_path, out_dir, prompt_override=prompt_override, category_name=category_name)
            state.update({"transcript_path": str(transcript_path), "transcript_done": True})
            _save_state(mov_path, state)
        except Exception as e:
            log.error("文字起こし失敗: %s", e)
            _notify_macos("video_processor エラー", f"文字起こし失敗: {mov_path.name}")
            # 失敗は記録しない → 次回再開時に再試行
    elif not skip_transcribe and not GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY 未設定のため文字起こしをスキップします")

    # 3. Notion 議事録追記
    if (transcript_path and minutes_prompt and NOTION_TOKEN and NOTION_MEMO_DB_ID
            and not state.get("notion_done")):
        try:
            transcript_text = transcript_path.read_text(encoding="utf-8")
            log.info("議事録を生成中...")
            minutes_text = generate_minutes_with_gemini(transcript_text, minutes_prompt)
            pdf_name = mov_path.stem
            log.info("Notionページを検索中: PDFName=%s", pdf_name)
            page_id = find_notion_page_by_pdf_name(pdf_name)
            if page_id:
                append_minutes_to_notion(page_id, minutes_text)
            else:
                log.warning("Notionへの追記をスキップ（ページが見つかりません）")
            state["notion_done"] = True
            _save_state(mov_path, state)
        except Exception as e:
            log.error("Notion議事録追記失敗: %s", e)
            # 失敗は記録しない → 次回再開時に再試行

    # 4. YouTube アップロード（最後：ネットワーク遮断されても他のステップは完了済み）
    if not skip_youtube and not state.get("youtube_done"):
        title = build_youtube_title(mov_path)
        try:
            youtube_id = upload_to_youtube(mov_path, title)
            state.update({"youtube_id": youtube_id, "youtube_done": True})
            _save_state(mov_path, state)
        except FileNotFoundError as e:
            log.warning("YouTube スキップ（認証ファイルなし）: %s", e)
            state["youtube_done"] = True
            _save_state(mov_path, state)
        except Exception as e:
            log.error("YouTube アップロード失敗: %s", e)
            # 失敗は記録しない → 次回再開時に再試行

    # 全ステップ完了チェック
    youtube_ok = skip_youtube or state.get("youtube_done", False)
    transcript_ok = skip_transcribe or not GEMINI_API_KEY or state.get("transcript_done", False)
    notion_ok = (
        not minutes_prompt or not NOTION_TOKEN or not NOTION_MEMO_DB_ID
        or state.get("notion_done", False)
    )
    all_done = (
        state.get("mp4_done") and state.get("m4a_done")
        and youtube_ok and transcript_ok and notion_ok
    )

    if not all_done:
        log.warning("一部のステップが未完了です。次回起動時に自動再開します: %s", mov_path.name)
        _notify_macos("動画処理: 一時中断", f"次回起動時に再開します: {mov_path.name}")
        return

    # 完了記録
    state["completed_at"] = datetime.now().isoformat()
    _save_state(mov_path, state)

    # 完了サマリー表示
    print("\n" + "=" * 60)
    print("処理完了!")
    print(f"  元ファイル  : {mov_path}")
    if mp4_path:
        print(f"  mp4 (圧縮) : {mp4_path}")
    if m4a_path:
        print(f"  m4a (音声) : {m4a_path}")
    if transcript_path:
        print(f"  文字起こし : {transcript_path}")
    if youtube_id:
        print(f"  YouTube    : https://youtu.be/{youtube_id}")
    if not transcript_path:
        print()
        print("次のステップ: m4a を NotebookLM にアップロードしてください")
    print("=" * 60 + "\n")

    # macOS の場合、出力フォルダを Finder で開く
    if sys.platform == "darwin":
        subprocess.run(["open", str(out_dir)], check=False)


# ─── フォルダ監視モード ───────────────────────────────────────────────────────

def watch_folder(
    watch_dir: Path, skip_youtube: bool = False, skip_transcribe: bool = False
) -> None:
    """
    フォルダを監視し、新しい .mov が書き込み完了したらユーザーに通知する。

    自動処理はせず、以下のフローで進む:
      1. 新規 .mov を検知 → macOS 通知 + ターミナルにメッセージ表示
      2. ユーザーが Finder でファイル名を整える
         （例: 20240327_山田太郎_候補者面談.mov）
      3. Enter を押すと未処理 .mov を列挙 → 確認後に処理開始

    watchdog ライブラリが利用可能な場合はイベントドリブン、
    なければポーリング（5秒間隔）にフォールバック。
    """
    import queue
    import threading

    watch_dir = watch_dir.expanduser().resolve()
    log.info("監視開始: %s", watch_dir)
    log.info("録画が終わったらファイル名を整えて Enter を押してください")

    # バックグラウンドスレッドから main スレッドへの通知キュー
    detected_q: queue.Queue = queue.Queue()
    _pending: set = set()  # キューまたはダイアログ処理中のファイルを追跡（重複防止）

    # 前回中断されたファイルを自動的にキューへ追加
    for _f in sorted(
        (p for s in _WATCHED_SUFFIXES for p in watch_dir.glob(f"*{s}")),
        key=lambda p: p.stat().st_mtime,
    ):
        _s = _load_state(_f)
        if _s is not None and _s.get("completed_at") is None:
            log.info("前回中断されたファイルを再開キューに追加: %s", _f.name)
            _pending.add(_f.resolve())
            detected_q.put(_f)

    def _on_new_mov(path: Path) -> None:
        """新規ファイル検知時にキューへ追加（バックグラウンドスレッドから呼ぶ）。"""
        resolved = path.resolve()
        _wait_until_stable(path)
        if not is_processed(path) and resolved not in _pending:
            _pending.add(resolved)
            detected_q.put(path)

    # ── watchdog でイベント監視 ──────────────────────────────────────────────
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        class MovHandler(FileSystemEventHandler):
            def _handle(self, path: str) -> None:
                p = Path(path)
                if p.suffix.lower() in _WATCHED_SUFFIXES:
                    threading.Thread(target=_on_new_mov, args=(p,), daemon=True).start()

            def on_created(self, event):
                if not event.is_directory:
                    self._handle(event.src_path)

            def on_moved(self, event):
                if not event.is_directory:
                    self._handle(event.dest_path)

        observer = Observer()
        observer.schedule(MovHandler(), str(watch_dir), recursive=False)
        observer.start()
        use_watchdog = True
    except ImportError:
        log.warning("watchdog が未インストール。ポーリングモードで監視します（pip install watchdog 推奨）")
        use_watchdog = False

    # ポーリングスレッド（watchdog がない場合のフォールバック）
    def _glob_watched(d: Path) -> set:
        return {p for s in _WATCHED_SUFFIXES for p in d.glob(f"*{s}")}

    if not use_watchdog:
        def _poll():
            known = _glob_watched(watch_dir)
            while True:
                try:
                    current = _glob_watched(watch_dir)
                    for p in (current - known):
                        _on_new_mov(p)
                    known = current
                except Exception as e:
                    log.error("ポーリングエラー: %s", e)
                time.sleep(5)

        threading.Thread(target=_poll, daemon=True).start()

    # ── メインループ: 検知通知を受け取りユーザー確認後に処理 ────────────────
    try:
        while True:
            try:
                detected_path = detected_q.get(timeout=1)
            except queue.Empty:
                continue

            _notify_and_wait_for_rename(
                watch_dir, detected_path, skip_youtube, skip_transcribe
            )
            _pending.discard(detected_path.resolve())

    except KeyboardInterrupt:
        log.info("監視を終了します")
        if use_watchdog:
            observer.stop()
            observer.join()


def _notify_and_wait_for_rename(
    watch_dir: Path, detected_path: Path, skip_youtube: bool, skip_transcribe: bool = False
) -> None:
    """
    新規 .mov 検知後、macOS ダイアログでファイル名整備を促し処理する。

    macOS ではターミナル不要のポップアップ UI で完結する。
    非 macOS ではターミナル入力にフォールバックする。
    """
    _send_macos_notification(
        title="録画ファイルを検出しました",
        message=f"{detected_path.name} — ファイル名を整えたら通知をクリック",
    )

    # macOS ダイアログでリネーム待ち
    if sys.platform == "darwin":
        result = _osascript_dialog(
            title="P&L 動画処理",
            message=(
                f"録画ファイルを検出しました:\n{detected_path.name}\n\n"
                "Finder でファイル名を変更してください。\n"
                "例: 20240327_山田太郎_候補者面談.mov\n\n"
                "準備ができたら OK を押してください。"
            ),
            buttons=["スキップ", "OK"],
            default_button="OK",
        )
        if result != "OK":
            log.info("スキップしました: %s", detected_path.name)
            return
    else:
        # 非 macOS フォールバック（ターミナル入力）
        print(f"\n録画ファイルを検出: {detected_path.name}")
        print("ファイル名を整えたら Enter を押してください（Ctrl+C でスキップ）")
        try:
            input()
        except KeyboardInterrupt:
            return

    # ダイアログ後にフォルダを再スキャン（リネーム済みのファイルを拾う）
    # リネーム直後はファイルシステムの反映に時間がかかる場合があるため最大5回リトライ
    pending = []
    for _retry in range(5):
        pending = sorted(
            [p for p in _glob_watched(watch_dir) if not is_processed(p)],
            key=lambda p: p.stat().st_mtime,
        )
        if pending:
            break
        log.info("再スキャン中... (%d/5)", _retry + 1)
        time.sleep(2)

    if not pending:
        log.info("処理対象のファイルが見つかりません。スキップします。")
        return

    if len(pending) == 1:
        target = pending[0]
    else:
        # 複数ある場合はダイアログで選択
        if sys.platform == "darwin":
            names = [f"{p.name}  ({p.stat().st_size/1e6:.0f} MB)" for p in pending]
            chosen = _osascript_choose_from_list(
                title="P&L 動画処理",
                prompt="処理するファイルを選択してください:",
                items=names,
            )
            if chosen is None:
                log.info("スキップしました。")
                return
            target = pending[names.index(chosen)]
        else:
            print("\n処理対象のファイルを選択してください:")
            for i, p in enumerate(pending, 1):
                print(f"  {i}. {p.name}  ({p.stat().st_size/1e6:.0f} MB)")
            try:
                raw = input("番号を入力 (s でスキップ): ").strip().lower()
                if raw == "s":
                    return
                target = pending[int(raw) - 1]
            except (ValueError, IndexError, KeyboardInterrupt):
                return

    # カテゴリ選択（再開時は保存済みカテゴリを使用してダイアログをスキップ）
    prompt_override = None
    minutes_prompt = None
    category_name = None
    existing_state = _load_state(target)
    if existing_state and existing_state.get("category_name"):
        category_name = existing_state["category_name"]
        log.info("前回のカテゴリで再開します: %s", category_name)
        if not skip_transcribe and GEMINI_API_KEY:
            if TRANSCRIPT_PROMPT_DIR:
                p_file = Path(TRANSCRIPT_PROMPT_DIR).expanduser() / f"{category_name}.txt"
                if p_file.exists():
                    prompt_override = p_file.read_text(encoding="utf-8").strip()
            minutes_prompt = _load_minutes_prompt(category_name)
    elif not skip_transcribe and GEMINI_API_KEY:
        prompt_override, category_name = _select_transcript_prompt()
        minutes_prompt = _load_minutes_prompt(category_name)

    try:
        process_file(
            target,
            skip_youtube=skip_youtube,
            skip_transcribe=skip_transcribe,
            prompt_override=prompt_override,
            minutes_prompt=minutes_prompt,
            category_name=category_name,
        )
    except Exception as e:
        log.error("処理失敗 %s: %s", target.name, e)
        if sys.platform == "darwin":
            _osascript_dialog(
                title="P&L 動画処理 - エラー",
                message=f"処理中にエラーが発生しました:\n{e}",
                buttons=["OK"],
                default_button="OK",
            )


def _osascript_dialog(
    title: str, message: str, buttons: list[str], default_button: str
) -> str:
    """macOS ダイアログを表示し、押されたボタン名を返す。"""
    buttons_str = ", ".join(f'"{b}"' for b in buttons)
    script = (
        f'display dialog "{message}" '
        f'buttons {{{buttons_str}}} '
        f'default button "{default_button}" '
        f'with title "{title}"'
    )
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True
    )
    # "button returned:OK" → "OK"
    out = result.stdout.strip()
    if out.startswith("button returned:"):
        return out.replace("button returned:", "")
    return ""


def _osascript_choose_from_list(title: str, prompt: str, items: list[str]) -> str | None:
    """macOS のリスト選択ダイアログを表示し、選択された文字列を返す。未選択は None。"""
    items_str = ", ".join(f'"{i}"' for i in items)
    script = (
        f'set choices to {{{items_str}}}\n'
        f'set selected to choose from list choices '
        f'with prompt "{prompt}" with title "{title}"\n'
        f'if selected is false then\n'
        f'    return "SKIP"\n'
        f'else\n'
        f'    return item 1 of selected\n'
        f'end if'
    )
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True
    )
    out = result.stdout.strip()
    return None if out == "SKIP" else out


def _send_macos_notification(title: str, message: str) -> None:
    """macOS の通知センターに通知を送る（非 macOS では何もしない）。"""
    if sys.platform != "darwin":
        return
    script = (
        f'display notification "{message}" with title "{title}" sound name "Glass"'
    )
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def _wait_until_stable(path: Path, interval: float = 2.0, retries: int = 10) -> None:
    """ファイルサイズが安定するまで待つ（録画中のファイルを処理しないため）。"""
    prev_size = -1
    for _ in range(retries):
        size = path.stat().st_size if path.exists() else 0
        if size == prev_size and size > 0:
            return
        prev_size = size
        time.sleep(interval)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Blackhole 録画 .mov ファイルを自動処理する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--no-youtube", action="store_true",
        help="YouTube アップロードをスキップ"
    )
    parser.add_argument(
        "--no-transcribe", action="store_true",
        help="Gemini 文字起こしをスキップ"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # process サブコマンド
    p_process = sub.add_parser("process", help="単体ファイルを処理")
    p_process.add_argument("mov_file", type=Path, help=".mov ファイルのパス")

    # watch サブコマンド
    p_watch = sub.add_parser("watch", help="フォルダを監視して自動処理")
    p_watch.add_argument("watch_dir", type=Path, help="監視するフォルダ")

    args = parser.parse_args()

    if args.command == "process":
        prompt_override = None
        minutes_prompt = None
        category_name = None
        existing_state = _load_state(args.mov_file.resolve())
        if existing_state and existing_state.get("category_name"):
            category_name = existing_state["category_name"]
            log.info("前回のカテゴリで再開します: %s", category_name)
            if not args.no_transcribe and GEMINI_API_KEY:
                if TRANSCRIPT_PROMPT_DIR:
                    p_file = Path(TRANSCRIPT_PROMPT_DIR).expanduser() / f"{category_name}.txt"
                    if p_file.exists():
                        prompt_override = p_file.read_text(encoding="utf-8").strip()
                minutes_prompt = _load_minutes_prompt(category_name)
        elif not args.no_transcribe and GEMINI_API_KEY:
            prompt_override, category_name = _select_transcript_prompt()
            minutes_prompt = _load_minutes_prompt(category_name)
        process_file(
            args.mov_file,
            skip_youtube=args.no_youtube,
            skip_transcribe=args.no_transcribe,
            prompt_override=prompt_override,
            minutes_prompt=minutes_prompt,
            category_name=category_name,
        )
    elif args.command == "watch":
        watch_folder(
            args.watch_dir,
            skip_youtube=args.no_youtube,
            skip_transcribe=args.no_transcribe,
        )


if __name__ == "__main__":
    main()
