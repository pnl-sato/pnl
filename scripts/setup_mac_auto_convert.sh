#!/bin/bash
# OGG自動変換のセットアップスクリプト
# 実行すると Mac ログイン時から自動変換が有効になる

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WATCHER_SCRIPT="$SCRIPT_DIR/mac_ogg_watcher.sh"
PLIST_LABEL="com.pnl.ogg-converter"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo "=== OGG自動変換セットアップ ==="

# 1. Homebrew確認
if ! command -v brew &>/dev/null; then
    echo "[ERROR] Homebrewがインストールされていません。"
    echo "  https://brew.sh からインストールしてください。"
    exit 1
fi

# 2. ffmpeg インストール
if ! command -v ffmpeg &>/dev/null; then
    echo "[1/3] ffmpeg をインストール中..."
    brew install ffmpeg
else
    echo "[1/3] ffmpeg: インストール済み ✓"
fi

# 3. fswatch インストール
if ! command -v fswatch &>/dev/null; then
    echo "[2/3] fswatch をインストール中..."
    brew install fswatch
else
    echo "[2/3] fswatch: インストール済み ✓"
fi

# 4. ウォッチャースクリプトに実行権限を付与
chmod +x "$WATCHER_SCRIPT"

# 5. launchd plist を作成
echo "[3/3] 自動起動を設定中..."
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$WATCHER_SCRIPT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/ogg-converter.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/ogg-converter.log</string>
</dict>
</plist>
EOF

# 6. launchd に登録（すでに登録済みなら再起動）
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo ""
echo "=== セットアップ完了 ==="
echo "~/Downloads に OGG ファイルを保存すると自動的に MP3 に変換されます。"
echo "ログ: ~/Library/Logs/ogg-converter.log"
echo ""
echo "停止する場合: launchctl unload $PLIST_PATH"
echo "再開する場合: launchctl load $PLIST_PATH"
