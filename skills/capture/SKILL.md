---
name: capture
description: Explicitly flag knowledge worth keeping — a hard-won fix, a non-obvious constraint, a correction to installed knowledge. The background distiller turns flags into staged candidates later.
disable-model-invocation: true
argument-hint: "[what you learned and why it was non-obvious]"
---

Flag this moment for the mneme distiller.

1. Resolve the mneme binary: `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` if `CLAUDE_PLUGIN_ROOT` is set, else `bin/mneme` from the repo checkout.
2. Run: `mneme flag "$ARGUMENTS"` — if the note describes installed knowledge being wrong or stale, add `--kind knowledge-issue`.
3. Confirm to the user that it is flagged and will be distilled at session end (or compaction). Do not distill now; do not summarize the session.
