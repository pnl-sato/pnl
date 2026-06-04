#!/usr/bin/env python3
"""PreToolUse 安全ガード（Bash 専用）。

CLAUDE.md / agents の運用ルールを「機械的な最後の砦」として強制する軽量フック。
標準入力で Claude Code から渡される PreToolUse ペイロード(JSON)を読み、Bash コマンドが
危険パターンに該当したら exit code 2 で**ブロック**（stderr の理由が Claude に渡る）。
該当しなければ exit 0 で素通し。

設計方針（settings.json で「安全ガード重点」を選択）:
- 誤爆を最小にする。判定は「実際のコマンド語」を見る（コマンドを ; | && 等で区切り、各区切りを
  シェル流にトークン分割して先頭語＝コマンド名を判定）。これにより
  `git commit -m "rm -rf / の説明"` のような**クォート内の文字列は誤検知しない**。
- 想定外の入力・JSON 破損時は **fail-open**（exit 0）。ガードの誤作動で業務を止めない。
- stdlib のみ（リポジトリ tools/ の方針に合わせる）。

止める対象:
1. 破壊的なファイル/デバイス操作（rm -rf の広域ターゲット、mkfs、dd of=/dev、forkbomb、
   chmod/chown -R を / や ~ に適用）
2. 候補者データ(candidates/)の git への混入（PII を git に出さない。CLAUDE.md §7）
   ※ -m コミットメッセージ内の文字列は対象外。実際のパス引数のみ判定。
3. 指定外ブランチへの push / main への force push（ブランチ規律。CLAUDE.md §12）

許可ブランチは環境変数 CLAUDE_ALLOWED_BRANCHES（カンマ区切り）で上書き可。
既定は main / master、および claude/ で始まる作業ブランチ。

限界（誤爆を抑えるための割り切り）:
- `bash -c "rm -rf /"` のように別シェルへ丸ごと文字列で渡す多重ネストは検知しない。
"""

import json
import os
import re
import shlex
import sys

_OPERATOR_SPLIT = re.compile(r"\|\||&&|\||;|&")
_ENV_ASSIGN = re.compile(r"^\w+=")
_LEADING_SKIP = {"sudo", "command", "env", "nice", "nohup", "time", "exec", "xargs", "then", "do", "else"}
_BROAD_TARGETS = {"/", "/*", "~", "~/", ".", "./", "..", "*", "$HOME", "${HOME}"}
_BROAD_ABS = re.compile(r"^/(home|etc|usr|var|bin|lib|lib64|boot|root|sbin|opt)/?$")


def _load_command() -> str:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return ""  # fail-open
    if payload.get("tool_name") != "Bash":
        return ""
    return (payload.get("tool_input") or {}).get("command", "") or ""


def _block(reason: str):
    sys.stderr.write(f"[bash_safety_guard] ブロックしました: {reason}\n")
    sys.exit(2)


def _segments(cmd: str):
    """コマンドを ; | && 等で区切り、各セグメントを (cmdword, args) に分解。"""
    out = []
    for raw in _OPERATOR_SPLIT.split(cmd):
        raw = raw.strip()
        if not raw:
            continue
        try:
            toks = shlex.split(raw, comments=False)
        except ValueError:
            toks = raw.split()
        i = 0
        while i < len(toks) and (_ENV_ASSIGN.match(toks[i]) or toks[i] in _LEADING_SKIP):
            i += 1
        if i >= len(toks):
            continue
        out.append((toks[i], toks[i + 1:], raw))
    return out


def _is_broad(arg: str) -> bool:
    return arg in _BROAD_TARGETS or bool(_BROAD_ABS.match(arg))


def _rm_has_recursive_force(args) -> bool:
    letters = ""
    for a in args:
        if a.startswith("-") and not a.startswith("--"):
            letters += a[1:]
    return ("r" in letters or "R" in letters) and "f" in letters


def _allowed_branches():
    extra = [b.strip() for b in os.environ.get("CLAUDE_ALLOWED_BRANCHES", "").split(",") if b.strip()]
    return {"main", "master", *extra}


def _branch_allowed(refspec: str) -> bool:
    b = refspec.lstrip("+").split(":")[-1].strip()
    if not b or b == "HEAD":
        return True
    if b.startswith("claude/"):
        return True
    return b in _allowed_branches()


def _check_segment(cmdword: str, args, raw: str):
    base = os.path.basename(cmdword)

    # 1) 破壊的操作
    if base == "rm" and _rm_has_recursive_force(args):
        if any(_is_broad(a) for a in args if not a.startswith("-")):
            _block("rm -rf が広域ターゲット（/, ~, ., * 等）を巻き込んでいます")
    if base.startswith("mkfs"):
        _block("mkfs（ファイルシステム作成）")
    if base == "dd" and any(a.startswith("of=/dev/") for a in args):
        _block("dd of=/dev/（デバイス直接書き込み）")
    if base in ("chmod", "chown") and any(a in ("-R", "-r", "--recursive") for a in args):
        if any(_is_broad(a) for a in args if not a.startswith("-")):
            _block(f"{base} -R を広域（/ や ~）に適用")
    if re.search(r">\s*/dev/sd[a-z]", raw, re.I):
        _block("/dev/sdX への直接リダイレクト")
    if re.search(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", raw):
        _block("forkbomb")

    # 2) 候補者PIIの git 混入 / 3) push 規律
    if base == "git" and args:
        sub = args[0]
        rest = args[1:]
        if sub in ("add", "commit", "stage"):
            cleaned, skip = [], False
            for a in rest:
                if skip:
                    skip = False
                    continue
                if a in ("-m", "--message", "-F", "--file"):
                    skip = True
                    continue
                if a.startswith("-m") and len(a) > 2:
                    continue
                cleaned.append(a)
            if any("candidates/" in a for a in cleaned):
                _block("candidates/（候補者PII）を git に追加/コミットしようとしています。"
                       "候補者プロファイルは Craft が唯一の正本（CLAUDE.md §7）")
        if sub == "push":
            force = any(a == "-f" or (a.startswith("--force") and a != "--force-with-lease") for a in rest)
            non_flags = [a for a in rest if not a.startswith("-")]
            if force and any(t in ("main", "master") for t in non_flags):
                _block("main/master への force push です（履歴破壊リスク）")
            if len(non_flags) >= 2 and not _branch_allowed(non_flags[1]):
                _block(f"指定外ブランチ '{non_flags[1]}' への push です。"
                       "作業は claude/ ブランチ、共有反映は main のみ（CLAUDE.md §12）")


def main():
    cmd = _load_command()
    if not cmd.strip():
        sys.exit(0)
    for cmdword, args, raw in _segments(cmd):
        _check_segment(cmdword, args, raw)
    sys.exit(0)


if __name__ == "__main__":
    main()
