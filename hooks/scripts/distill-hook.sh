#!/usr/bin/env bash
# Stop/PreCompact hook: fire-and-forget distillation trigger (spec §7.2, §9).
# Exits 0 on every path; never blocks the session; never emits decision JSON.
set -u
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

[ -n "${MNEME_DISTILLING:-}" ] && exit 0

PAYLOAD="$(cat 2>/dev/null || true)"
TRANSCRIPT="$(MNEME_HOOK_PAYLOAD="$PAYLOAD" python3 - <<'PY' 2>/dev/null || true
import json
import os

try:
    data = json.loads(os.environ.get("MNEME_HOOK_PAYLOAD") or "{}")
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
