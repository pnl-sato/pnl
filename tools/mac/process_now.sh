#!/bin/zsh
# 録画/録音を手動で動画処理パイプラインに通すラッパー
# Finderクイックアクション「動画処理」から引数でファイルを受け取る（複数可・スペース込みOK）。
# 引数なしで起動すると choose file ダイアログでファイル選択。
#
# ★ffprobe はコード内で PATH 依存のベタ呼び出しがあるため、Homebrew を PATH 先頭に入れる。
export PATH="$(brew --prefix)/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# このスクリプト(tools/mac/)の位置から解決（両Mac・ユーザ非依存）
HERE="${0:A:h}"
REPO_ROOT="${HERE:h:h}"
PYTHON="$REPO_ROOT/venv/bin/python"
SCRIPT="$HERE/video_processor.py"
LOG="/tmp/process_now.log"

notify() {
  # $1=タイトル $2=本文
  /usr/bin/osascript -e "display notification \"$2\" with title \"$1\"" >/dev/null 2>&1
}

# iCloud dataless（クラウド退避でローカル0B）なら実体化を待つ
ensure_downloaded() {
  local f="$1"
  if ls -lO "$f" 2>/dev/null | grep -q dataless; then
    notify "動画処理" "☁️ iCloudから実体をダウンロード中: ${f:t}"
    /usr/bin/brctl download "$f" 2>/dev/null
    # dataless が消えるまで5秒間隔で待つ（約60分でタイムアウト = 720回）
    local i=0
    while ls -lO "$f" 2>/dev/null | grep -q dataless; do
      i=$((i + 1))
      if [ "$i" -ge 720 ]; then
        notify "動画処理" "❌ ダウンロードがタイムアウト: ${f:t}"
        return 1
      fi
      sleep 5
    done
  fi
  return 0
}

process_one() {
  local f="$1"
  if [ ! -e "$f" ]; then
    notify "動画処理" "❌ ファイルが見つかりません: ${f:t}"
    return
  fi

  ensure_downloaded "$f" || return

  notify "動画処理" "▶️ 処理を開始: ${f:t}"

  # ログをクリアしてから実行（既処理判定を末尾で行うため）
  : > "$LOG"
  "$PYTHON" "$SCRIPT" process "$f" >>"$LOG" 2>&1
  local rc=$?

  if grep -q "処理済みのためスキップ" "$LOG"; then
    notify "動画処理" "⏭ 既に処理済み: ${f:t}"
  elif [ "$rc" -eq 0 ]; then
    notify "動画処理" "✅ 完了: ${f:t}"
  else
    notify "動画処理" "❌ エラー (rc=$rc): ${f:t}  ログ: $LOG"
  fi
}

# ── ファイルの収集 ──────────────────────────────────────────────
typeset -a FILES
if [ "$#" -gt 0 ]; then
  FILES=("$@")
else
  # 引数なし起動 → choose file ダイアログ（複数選択可）
  local chosen
  chosen=$(/usr/bin/osascript <<'OSA'
set theFiles to choose file with prompt "処理するファイルを選択（複数可）" of type {"mov","mp4","m4a","mp3","wav"} with multiple selections allowed
set out to ""
repeat with f in theFiles
  set out to out & POSIX path of f & linefeed
end repeat
return out
OSA
)
  [ -z "$chosen" ] && exit 0
  FILES=("${(@f)chosen}")
fi

for f in "${FILES[@]}"; do
  [ -z "$f" ] && continue
  process_one "$f"
done
