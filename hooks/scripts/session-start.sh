#!/usr/bin/env bash
# SessionStart hook: inject the mneme noticing brief + registry summary.
# Contract (docs/research/2026-08-11-claude-code-plugin-wiring.md): emit ONLY
# the hookSpecificOutput JSON form; exit 0 on every path — a broken mneme
# must never break a session.
set -u
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
OUT="$("$ROOT/bin/mneme" context 2>/dev/null)" || exit 0
[ -z "$OUT" ] && exit 0
MNEME_CONTEXT_TEXT="$OUT" python3 - <<'PY' 2>/dev/null || exit 0
import json
import os

print(
    json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": os.environ["MNEME_CONTEXT_TEXT"],
            }
        }
    )
)
PY
exit 0
