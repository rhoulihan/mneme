#!/usr/bin/env bash
# SessionStart hook: inject the mneme noticing brief + registry summary.
# Contract (docs/research/2026-08-11-claude-code-plugin-wiring.md): emit ONLY
# the hookSpecificOutput JSON form; exit 0 on every path — a broken mneme
# must never break a session.
set -u
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

# The distiller runs a nested headless session with --allowedTools "Read,Grep,Glob". It
# cannot flag anything, so the noticing brief is noise to it and the tally line is an
# instruction it is unable to obey. Mirrors the guard in distill-hook.sh.
[ -n "${MNEME_DISTILLING:-}" ] && exit 0

# The payload goes through a temp file for the same reason distill-hook.sh does:
# a hook payload can exceed the per-string exec limit, and an E2BIG there would
# be swallowed by our own tolerance for failure. Garbage or absent cwd → empty.
PAYLOAD_FILE="$(mktemp)"
trap 'rm -f "$PAYLOAD_FILE"' EXIT
cat > "$PAYLOAD_FILE" 2>/dev/null || true
FIELDS="$(MNEME_HOOK_PAYLOAD_FILE="$PAYLOAD_FILE" python3 - <<'PY' 2>/dev/null || true
import json
import os

try:
    with open(os.environ["MNEME_HOOK_PAYLOAD_FILE"], encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    raise SystemExit(0)
print(data.get("cwd", ""), data.get("session_id", ""),
      data.get("source", ""), sep="\t")
PY
)"
# Tab-separated, split with IFS so a missing trailing field is empty rather than a copy
# of an earlier one: `${FIELDS#*$TAB}` returns the WHOLE string when no tab is present,
# which would have filed every session under a path-shaped id.
IFS=$'\t' read -r SESSION_CWD SESSION_ID SESSION_SOURCE <<<"$FIELDS" || true
SESSION_CWD="${SESSION_CWD:-}"
SESSION_ID="${SESSION_ID:-}"
SESSION_SOURCE="${SESSION_SOURCE:-}"
# --session excludes THIS session from its own tally: a resume would otherwise be told
# about itself.
set --
[ -n "$SESSION_CWD" ] && set -- "$@" --cwd "$SESSION_CWD"
[ -n "$SESSION_ID" ] && set -- "$@" --session "$SESSION_ID"
# compact/clear/resume continue the SAME session, so `context` reports that session's own
# tally instead of excluding it and speaking an older one.
[ -n "$SESSION_SOURCE" ] && set -- "$@" --source "$SESSION_SOURCE"
OUT="$("$ROOT/bin/mneme" context "$@" 2>/dev/null)" || exit 0
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
