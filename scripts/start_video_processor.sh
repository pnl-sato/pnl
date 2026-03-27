#!/bin/bash
# P&L 動画処理スクリプト 起動ラッパー
# launchd から呼び出される

# launchd は通常の PATH を持たないため Homebrew のパスを明示的に追加
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$SCRIPT_DIR/venv/bin/activate"
LOG="$SCRIPT_DIR/video_processor.log"

source "$VENV"
exec python3 "$SCRIPT_DIR/scripts/video_processor.py" watch ~/Desktop >> "$LOG" 2>&1
