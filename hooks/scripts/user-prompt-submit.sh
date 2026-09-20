#!/usr/bin/env bash
# UserPromptSubmit hook: put what mneme noticed in front of the model.
#
# This is one of only two events that can inject context (verified against Claude Code
# 2.1.278 -- `Stop` cannot, and its stdout is discarded), and the only one that fires
# repeatedly DURING a session. It is also the right moment on its own terms: the
# requirements doc's RC3 says "at the moment it happens" is the worst moment, because the
# flag competes with processing the finding. A turn boundary is not that.
#
# Exit 0 on every path; a broken mneme must never break a session.
set -u
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

[ -n "${MNEME_DISTILLING:-}" ] && exit 0

PAYLOAD_FILE="$(mktemp)"
trap 'rm -f "$PAYLOAD_FILE"' EXIT
cat > "$PAYLOAD_FILE" 2>/dev/null || true
SESSION_ID="$(MNEME_HOOK_PAYLOAD_FILE="$PAYLOAD_FILE" python3 - <<'PY' 2>/dev/null || true
import json
import os

try:
    with open(os.environ["MNEME_HOOK_PAYLOAD_FILE"], encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    raise SystemExit(0)
print(data.get("session_id", ""))
PY
)"
[ -z "$SESSION_ID" ] && exit 0

OUT="$("$ROOT/bin/mneme" session prompt --session "$SESSION_ID" 2>/dev/null)" || exit 0
[ -z "$OUT" ] && exit 0
MNEME_PROMPT_TEXT="$OUT" python3 - <<'PY' 2>/dev/null || exit 0
import json
import os

print(
    json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": os.environ["MNEME_PROMPT_TEXT"],
            }
        }
    )
)
PY
exit 0
