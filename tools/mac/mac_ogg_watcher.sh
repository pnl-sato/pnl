#!/bin/bash
# OGG自動変換ウォッチャー
# Chromeのダウンロード先フォルダを監視して OGG を自動的に MP3 に変換する

WATCH_DIR="/Users/satouyuuta/Desktop/00_Download_sync"
LOG="$HOME/Library/Logs/ogg-converter.log"
FFMPEG="$(brew --prefix)/bin/ffmpeg"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ウォッチャー起動: $WATCH_DIR" >> "$LOG"

fswatch -0 --event Created --event Renamed "$WATCH_DIR" | while IFS= read -r -d '' file; do
    # 拡張子が .ogg のファイルのみ処理
    [[ "${file,,}" == *.ogg ]] || continue

    # ファイルの書き込みが完了するまで少し待つ
    sleep 1

    output="${file%.ogg}.mp3"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 変換開始: $(basename "$file")" >> "$LOG"

    "$FFMPEG" -i "$file" -q:a 2 "$output" -y >> "$LOG" 2>&1

    if [ $? -eq 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 変換完了: $(basename "$output")" >> "$LOG"
        rm "$file"  # 元のOGGを削除
        # 通知を送る
        osascript -e "display notification \"$(basename "$output")\" with title \"OGG → MP3 変換完了\""
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 変換失敗: $(basename "$file")" >> "$LOG"
        osascript -e "display notification \"$(basename "$file")\" with title \"OGG変換に失敗しました\""
    fi
done
