---
name: verify
description: Run the staleness sweep over a registered knowledge plugin and help re-verify what it finds.
disable-model-invocation: true
argument-hint: [plugin-name] [--days N]
---

1. Run `mneme verify $ARGUMENTS` (binary at `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`). Exit 2 means stale units were found — that is a report, not an error.
2. Present the stale units grouped by kind. For each, offer to help the user re-verify: check whether the procedure still works or the fact still holds.
3. When the user confirms a unit is still accurate or needs updating, flag it (`mneme flag ...` / `--kind knowledge-issue`) so the correction flows through the normal distill → share pipeline. Never edit knowledge repos directly.
