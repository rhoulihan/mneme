#!/usr/bin/env bash
# Stop/PreCompact hook: fire-and-forget distillation trigger (spec §7.2, §9).
# Exits 0 on every path; never blocks the session; never emits decision JSON.
set -u
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

[ -n "${MNEME_DISTILLING:-}" ] && exit 0

# The payload goes through a temp file, never an environment variable: Stop
# payloads carry last_assistant_message and routinely exceed Linux's ~128KB
# per-string exec limit, and an E2BIG there is swallowed by our own `|| true`
# — distillation would silently switch off on exactly the long sessions worth
# distilling.
PAYLOAD_FILE="$(mktemp)"
trap 'rm -f "$PAYLOAD_FILE"' EXIT
cat > "$PAYLOAD_FILE" 2>/dev/null || true
TRANSCRIPT="$(MNEME_HOOK_PAYLOAD_FILE="$PAYLOAD_FILE" python3 - <<'PY' 2>/dev/null || true
import json
import os

try:
    with open(os.environ["MNEME_HOOK_PAYLOAD_FILE"], encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    raise SystemExit(1)
if data.get("stop_hook_active"):
    raise SystemExit(1)
print(data.get("transcript_path", ""))
PY
)"
[ -z "$TRANSCRIPT" ] && exit 0

"$ROOT/bin/mneme" distill pending >/dev/null 2>&1 || exit 0

if [ "${MNEME_DISTILL_FOREGROUND:-}" = "1" ]; then
  "$ROOT/bin/mneme-distill-pipeline" "$TRANSCRIPT" >/dev/null 2>&1 || true
else
  LOG_DIR="${MNEME_HOME:-$HOME/.mneme}/logs"
  mkdir -p "$LOG_DIR" 2>/dev/null || true
  nohup "$ROOT/bin/mneme-distill-pipeline" "$TRANSCRIPT" \
    >>"$LOG_DIR/distill.log" 2>&1 &
fi
exit 0
