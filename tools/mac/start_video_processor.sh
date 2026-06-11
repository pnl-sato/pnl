#!/bin/bash
# P&L 動画処理スクリプト 起動ラッパー（launchd から呼び出される）
# 配置: <repo>/tools/mac/start_video_processor.sh

# launchd は通常の PATH を持たないため Homebrew のパスを明示的に追加
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"

# このスクリプトは tools/mac/ にあるので 2つ上がリポジトリルート
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENV="$REPO_ROOT/venv/bin/activate"
LOG="$REPO_ROOT/video_processor.log"

source "$VENV"
exec python3 "$REPO_ROOT/tools/mac/video_processor.py" watch ~/Desktop >> "$LOG" 2>&1
