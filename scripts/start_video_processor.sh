#!/bin/bash
# P&L 動画処理スクリプト 起動ラッパー
# launchd から呼び出される

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$SCRIPT_DIR/venv/bin/activate"
LOG="$SCRIPT_DIR/video_processor.log"

source "$VENV"
exec python3 "$SCRIPT_DIR/scripts/video_processor.py" watch ~/Desktop >> "$LOG" 2>&1
