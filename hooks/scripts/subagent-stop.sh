#!/usr/bin/env bash
# SubagentStop hook: mine the report a delegated agent just returned.
#
# T1, the requirements doc's highest-value trigger: "in the observed session, essentially
# every durable fact arrived this way." SubagentStop CANNOT inject context into the parent
# (verified against Claude Code 2.1.278) -- but it can SEE, and the report arrives in the
# payload as `last_assistant_message`. So this records; UserPromptSubmit speaks.
#
# Exit 0 on every path; a broken mneme must never break a session.
set -u
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

[ -n "${MNEME_DISTILLING:-}" ] && exit 0

PAYLOAD_FILE="$(mktemp)"
REPORT_FILE="$(mktemp)"
trap 'rm -f "$PAYLOAD_FILE" "$REPORT_FILE"' EXIT
cat > "$PAYLOAD_FILE" 2>/dev/null || true

# The report can be tens of kilobytes (60KB observed), so it goes to a file rather than
# through an environment variable or an argument -- the same reason distill-hook.sh takes
# its payload that way.
FIELDS="$(MNEME_HOOK_PAYLOAD_FILE="$PAYLOAD_FILE" MNEME_REPORT_FILE="$REPORT_FILE" \
  python3 - <<'PY' 2>/dev/null || true
import json
import os

try:
    with open(os.environ["MNEME_HOOK_PAYLOAD_FILE"], encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    raise SystemExit(1)
if not isinstance(data, dict):
    raise SystemExit(1)
with open(os.environ["MNEME_REPORT_FILE"], "w", encoding="utf-8") as f:
    f.write(str(data.get("last_assistant_message") or ""))
print(data.get("session_id", ""), data.get("agent_type", ""), sep="\t")
PY
)"
IFS=$'\t' read -r SESSION_ID AGENT_TYPE <<<"$FIELDS" || true
SESSION_ID="${SESSION_ID:-}"
AGENT_TYPE="${AGENT_TYPE:-}"
[ -z "$SESSION_ID" ] && exit 0
[ -s "$REPORT_FILE" ] || exit 0

"$ROOT/bin/mneme" session report --session "$SESSION_ID" \
  ${AGENT_TYPE:+--agent-type "$AGENT_TYPE"} <"$REPORT_FILE" >/dev/null 2>&1 || true
exit 0
