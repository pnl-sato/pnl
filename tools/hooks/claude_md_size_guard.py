#!/usr/bin/env python3
"""PostToolUse hook (Edit matcher): warn when CLAUDE.md exceeds 200 lines.

Runs after Edit tool calls. Checks if the edited file is CLAUDE.md and if so,
warns when it exceeds the recommended line count. Never blocks (exit 0 always).
"""
import json
import os
import sys


def main():
    tool_input = json.loads(os.environ.get("TOOL_INPUT", "{}"))
    file_path = tool_input.get("file_path", "")

    if not file_path.endswith("CLAUDE.md"):
        return

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    claude_md = os.path.join(project_dir, "CLAUDE.md") if project_dir else file_path

    if not os.path.isfile(claude_md):
        return

    with open(claude_md, "r", encoding="utf-8") as f:
        line_count = sum(1 for _ in f)

    if line_count > 200:
        print(
            f"⚠ CLAUDE.md is {line_count} lines (recommended: ≤200). "
            f"Consider moving detailed procedures to agents/*.md or .claude/rules/.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
