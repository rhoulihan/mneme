---
name: adopt
description: Retrofit mneme governance onto an existing registered knowledge repo — scope statement, contribution rubric, CODEOWNERS, CI — adding only what is missing.
disable-model-invocation: true
argument-hint: [plugin-name]
---

1. If the repo is not yet registered, register it first: `mneme registry add <name> --repo <url> --clone` (or `--path` for an existing checkout).
2. Ask the user what this plugin's scope should be (what belongs, what does not), then run `mneme adopt $ARGUMENTS --description "<their scope>" --owner "<their team>"` (binary at `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`).
3. Report exactly which files were added, surface any lint warning about legacy content, and remind the user the changes are uncommitted — they review and commit through their repo's normal process.
