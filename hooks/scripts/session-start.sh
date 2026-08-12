#!/usr/bin/env bash
# SessionStart hook: inject the mneme noticing brief + registry summary.
# Contract (docs/research/2026-08-11-claude-code-plugin-wiring.md): emit ONLY
# the hookSpecificOutput JSON form; exit 0 on every path — a broken mneme
# must never break a session.
set -u
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

# The payload goes through a temp file for the same reason distill-hook.sh does:
# a hook payload can exceed the per-string exec limit, and an E2BIG there would
# be swallowed by our own tolerance for failure. Garbage or absent cwd → empty.
PAYLOAD_FILE="$(mktemp)"
trap 'rm -f "$PAYLOAD_FILE"' EXIT
cat > "$PAYLOAD_FILE" 2>/dev/null || true
SESSION_CWD="$(MNEME_HOOK_PAYLOAD_FILE="$PAYLOAD_FILE" python3 - <<'PY' 2>/dev/null || true
import json
import os

try:
    with open(os.environ["MNEME_HOOK_PAYLOAD_FILE"], encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    raise SystemExit(0)
print(data.get("cwd", ""))
PY
)"
if [ -n "$SESSION_CWD" ]; then
  OUT="$("$ROOT/bin/mneme" context --cwd "$SESSION_CWD" 2>/dev/null)" || exit 0
else
  OUT="$("$ROOT/bin/mneme" context 2>/dev/null)" || exit 0
fi
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
