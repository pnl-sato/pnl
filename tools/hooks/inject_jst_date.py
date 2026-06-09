#!/usr/bin/env python3
"""SessionStart hook: inject the authoritative current date in JST.

Why this exists:
  The remote execution container runs in UTC. The harness-provided
  "Today's date" is computed in UTC, so it lags Japan local time
  (JST = UTC+9) by up to 9 hours: between 00:00 and 09:00 JST it still
  shows the *previous* calendar day. Sato works in Japan, so JST is the
  source of truth for every date/time judgment (deadlines, "today /
  tomorrow / last week", send timing, scheduling).

What it does:
  Prints the correct current JST date into the session context as
  additionalContext, so it overrides the stale UTC "Today's date" value.

Implementation note:
  JST is computed as a fixed UTC+9 offset using only the standard library,
  so it works even if the Asia/Tokyo tz database is not installed in the
  container.
"""
import json
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9), "JST")
now = datetime.now(JST)
today = now.strftime("%Y-%m-%d")
stamp = now.strftime("%Y-%m-%d (%a) %H:%M JST")

context = (
    f"【日付の正本（JST）】本日は {stamp}。"
    f"harness が注入する \"Today's date\" は UTC 基準のため JST より最大9時間遅れ、"
    f"日本時間の午前0〜9時台は前日を指すことがあります。"
    f"日付・時刻に関する判断は、必ずこの JST の値（本日 = {today}）を最優先で使うこと。"
)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context,
    }
}, ensure_ascii=False))
