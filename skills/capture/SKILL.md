---
name: capture
description: Explicitly flag knowledge worth keeping — a hard-won fix, a non-obvious constraint, a correction to installed knowledge. The background distiller turns flags into staged candidates later.
disable-model-invocation: true
argument-hint: "[what you learned and why it was non-obvious]"
---

Flag this moment for the mneme distiller.

1. Resolve the mneme binary: `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` if `CLAUDE_PLUGIN_ROOT` is set, else `bin/mneme` from the repo checkout.
2. Prefer the **`mneme_flag` MCP tool** when it is available: `mneme_flag(text="$ARGUMENTS", kind="golden-path")`, or `kind="knowledge-issue"` if the note describes installed knowledge being wrong or stale. It takes the text as-is — quotes, `$`, backslashes and newlines need no escaping, which is the failure the shell path keeps hitting.
   Otherwise run: `mneme flag "$ARGUMENTS"` — add `--kind knowledge-issue` for the second case.
3. Confirm to the user that it is flagged and will be distilled at session end (or compaction). Do not distill now; do not summarize the session.
