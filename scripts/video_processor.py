#!/usr/bin/env python3
"""
動画自動処理スクリプト

Blackholeで録画した .mov ファイルを監視し、以下を自動実行：
1. ffmpeg で圧縮 mp4 + m4a 音声ファイルを生成
2. mp4 を YouTube に自動アップロード（アーカイブ）
3. m4a を出力フォルダへ配置（NotebookLM アップロード用）

必要な準備:
  brew install ffmpeg
  pip install -r requirements.txt
  .env に YOUTUBE_CLIENT_SECRETS_FILE を設定（初回のみブラウザ認証が走る）

使い方:
  # 単体処理
  python video_processor.py process /path/to/recording.mov

  # フォルダ監視（新規 .mov を自動処理）
  python video_processor.py watch ~/Movies/Recordings
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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

# 処理済みファイルの記録（二重処理防止）
PROCESSED_LOG = os.getenv("PROCESSED_LOG", "~/.video_processor_done.json")


# ─── ffmpeg 処理 ──────────────────────────────────────────────────────────────

def run_ffmpeg(args: list[str]) -> None:
    """ffmpeg コマンドを実行し、失敗時は例外を投げる。"""
    cmd = ["ffmpeg", "-y"] + args
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

    # m4a 抽出（音声のみ）
    run_ffmpeg([
        "-i", str(mov_path),
        "-vn",
        "-c:a", "aac",
        "-b:a", "128k",
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


def upload_to_youtube(mp4_path: Path, title: str, description: str = "") -> str:
    """
    mp4 を YouTube にアップロードし、動画 ID を返す。

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

    media = MediaFileUpload(str(mp4_path), mimetype="video/mp4", resumable=True)

    log.info("YouTube アップロード開始: %s", title)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            log.info("アップロード進捗: %d%%", pct)

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
    return str(mov_path.resolve()) in _load_processed()


def mark_processed(mov_path: Path, youtube_id: str, m4a_path: Path) -> None:
    data = _load_processed()
    data[str(mov_path.resolve())] = {
        "processed_at": datetime.now().isoformat(),
        "youtube_id": youtube_id,
        "m4a_path": str(m4a_path),
    }
    _save_processed(data)


# ─── タイトル生成 ─────────────────────────────────────────────────────────────

def build_youtube_title(mov_path: Path) -> str:
    """
    ファイル名から YouTube タイトルを生成する。

    Blackhole / QuickTime の録画ファイル名例:
      "2024-03-27 10-30-00.mov"
      "候補者面談_山田太郎_2024-03-27.mov"
    """
    stem = mov_path.stem
    # 日付パターンを見つけて整形
    date_match = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})", stem)
    if date_match:
        y, m, d = date_match.groups()
        date_str = f"{y}/{m}/{d}"
    else:
        date_str = datetime.now().strftime("%Y/%m/%d")

    return f"[録画] {stem} ({date_str})"


# ─── メイン処理 ───────────────────────────────────────────────────────────────

def process_file(mov_path: Path, skip_youtube: bool = False) -> None:
    """1つの .mov ファイルを処理する。"""
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

    # 1. ffmpeg 変換
    mp4_path, m4a_path = convert_mov_to_mp4_and_m4a(mov_path, out_dir)

    # 2. YouTube アップロード
    youtube_id = ""
    if not skip_youtube:
        title = build_youtube_title(mov_path)
        try:
            youtube_id = upload_to_youtube(mp4_path, title)
        except FileNotFoundError as e:
            log.warning("YouTube スキップ（認証ファイルなし）: %s", e)
        except Exception as e:
            log.error("YouTube アップロード失敗: %s", e)

    # 3. 処理済み記録
    mark_processed(mov_path, youtube_id, m4a_path)

    # 4. 完了通知
    print("\n" + "=" * 60)
    print("処理完了!")
    print(f"  元ファイル : {mov_path}")
    print(f"  mp4 (圧縮): {mp4_path}")
    print(f"  m4a (音声): {m4a_path}")
    if youtube_id:
        print(f"  YouTube   : https://youtu.be/{youtube_id}")
    print()
    print("次のステップ: m4a を NotebookLM にアップロードしてください")
    print("=" * 60 + "\n")

    # macOS の場合、m4a フォルダを Finder で開く
    if sys.platform == "darwin":
        subprocess.run(["open", str(out_dir)], check=False)


# ─── フォルダ監視モード ───────────────────────────────────────────────────────

def watch_folder(watch_dir: Path, skip_youtube: bool = False) -> None:
    """
    フォルダを監視し、新しい .mov ファイルが完全に書き込まれたら処理する。

    watchdog ライブラリが利用可能な場合はイベントドリブン、
    なければポーリング（5秒間隔）にフォールバック。
    """
    watch_dir = watch_dir.expanduser().resolve()
    log.info("監視開始: %s", watch_dir)

    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        class MovHandler(FileSystemEventHandler):
            def on_created(self, event):
                if event.is_directory:
                    return
                path = Path(event.src_path)
                if path.suffix.lower() != ".mov":
                    return
                # 書き込み完了まで少し待つ
                _wait_until_stable(path)
                try:
                    process_file(path, skip_youtube=skip_youtube)
                except Exception as e:
                    log.error("処理失敗 %s: %s", path.name, e)

        observer = Observer()
        observer.schedule(MovHandler(), str(watch_dir), recursive=False)
        observer.start()
        log.info("watchdog で監視中（Ctrl+C で終了）")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()

    except ImportError:
        log.warning("watchdog が未インストール。ポーリングモードで監視します（pip install watchdog を推奨）")
        _poll_folder(watch_dir, skip_youtube)


def _wait_until_stable(path: Path, interval: float = 2.0, retries: int = 10) -> None:
    """ファイルサイズが安定するまで待つ（録画中のファイルを処理しないため）。"""
    prev_size = -1
    for _ in range(retries):
        size = path.stat().st_size if path.exists() else 0
        if size == prev_size and size > 0:
            return
        prev_size = size
        time.sleep(interval)


def _poll_folder(watch_dir: Path, skip_youtube: bool, interval: int = 5) -> None:
    """ポーリングでフォルダを監視する（watchdog の代替）。"""
    known = set(watch_dir.glob("*.mov"))
    while True:
        try:
            current = set(watch_dir.glob("*.mov"))
            new_files = current - known
            for path in new_files:
                _wait_until_stable(path)
                try:
                    process_file(path, skip_youtube=skip_youtube)
                except Exception as e:
                    log.error("処理失敗 %s: %s", path.name, e)
            known = current
        except Exception as e:
            log.error("ポーリングエラー: %s", e)
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
        help="YouTube アップロードをスキップ（変換のみ）"
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
        process_file(args.mov_file, skip_youtube=args.no_youtube)
    elif args.command == "watch":
        watch_folder(args.watch_dir, skip_youtube=args.no_youtube)


if __name__ == "__main__":
    main()
