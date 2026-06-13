#!/usr/bin/env python3
"""PostToolUse hook: nudge to log write failures into the collective-knowledge ledgers.

Why this exists:
  Craft / Notion write failures keep recurring across sessions, but the
  failure->success cases only accumulate if someone remembers to append a
  row to the right ledger. This hook watches the write tools and, when a
  tool result carries a known *hard-error* signature, injects a reminder
  pointing at the correct ledger + recovery rule, so the knowledge actually
  gets captured instead of evaporating with the session.

Scope / honest limitation:
  This can only catch *hard errors* the tool surfaces in its response
  (e.g. `requires approval`, `Unexpected HTML token`, generic error flags).
  It CANNOT catch *silent* format corruption -- e.g. Craft saving `\\n` as a
  literal, or bullet styles being dropped -- because those return success.
  Those still rely on the post-write `blocks get` verification habit
  (craft-writing.md 3-4). The hook is a safety net for loud failures only.

Routing (mirrors agents/craft-writing.md and agents/notion-writing.md):
  - craft_write failure          -> agents/craft-writing.md  (case ledger 0)
  - notion-* write failure        -> agents/notion-writing.md (case ledger 0)
  - `requires approval` wedge      -> CLAUDE.md 14 (don't retry >1; isolate
                                      via one read; evacuate; new session)

Behavior:
  Fail-open. Any parsing/error path exits 0 with no output so the tool
  workflow is never blocked by this hook.
"""
import json
import sys

# Hard-error signatures we can reliably detect in a tool response.
WEDGE_SIGNATURES = ("requires approval", "streamable http error")
FORMAT_SIGNATURES = ("unexpected html token",)
GENERIC_ERROR_SIGNATURES = ("\"iserror\": true", "'iserror': true", "is_error\": true")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return  # fail-open: malformed input, do nothing

    tool_name = (payload.get("tool_name") or "").lower()

    # Which ledger does this write target belong to?
    if "craft_write" in tool_name:
        medium = "craft"
        ledger = "agents/craft-writing.md の §0 ケース台帳"
    elif "notion-create-pages" in tool_name or "notion-update-page" in tool_name \
            or "notion-create-comment" in tool_name or "notion-duplicate-page" in tool_name \
            or "notion-update-data-source" in tool_name or "notion-create-database" in tool_name:
        medium = "notion"
        ledger = "agents/notion-writing.md の §0 ケース台帳"
    else:
        return  # not a write tool we track

    # Flatten the tool response to a searchable string.
    blob = json.dumps(payload.get("tool_response", ""), ensure_ascii=False).lower()

    is_wedge = any(sig in blob for sig in WEDGE_SIGNATURES)
    is_format = any(sig in blob for sig in FORMAT_SIGNATURES)
    is_generic = any(sig in blob for sig in GENERIC_ERROR_SIGNATURES)

    if not (is_wedge or is_format or is_generic):
        return  # write looks fine (or only silently corrupt -- can't detect that here)

    lines = [
        f"【書き込み失敗を検知（{medium}）】このツール応答に失敗シグネチャが含まれています。",
    ]
    if is_wedge:
        lines.append(
            "・`requires approval` の wedge と判断。CLAUDE.md §14 に従う："
            "リトライは最大1回／読み取り1本で生死を切り分け／成果物と保存仕様を Craft "
            "`_引き継ぎ｜未保存タスク` に退避／ユーザーに通知（チャットに承認ダイアログは出ない）／新セッションで再開。"
        )
    if is_format:
        lines.append(
            "・`Unexpected HTML token` 系の書式エラー。callout/特殊記法を本文と混在させていないか確認し、単独 add に分ける。"
        )
    if is_generic and not (is_wedge or is_format):
        lines.append("・エラーフラグを検知。応答の error 内容を確認して原因を特定する。")
    lines.append(
        f"・解決したら（ユーザー指示を待たず）{ledger} に1行追記する"
        "（日付・セッション／失敗症状／トリガー／成功手順／恒久ルール参照）。"
        "媒体横断の wedge は実体を §14 に置き、台帳には索引行のみ。"
    )

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n".join(lines),
        }
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail-open: never block the tool workflow
