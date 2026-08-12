---
name: status
description: Show the mneme pipeline state — registered knowledge plugins, pending flags, staged and quarantined candidates, submissions, index freshness.
disable-model-invocation: true
---

Run `mneme status` (binary at `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`) and present the output faithfully. If candidates are staged, remind the user that `/mneme:share` reviews them. If flags are pending, note they distill at session end. Add nothing speculative.
