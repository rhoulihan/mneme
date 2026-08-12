---
name: new
description: Create a new governed knowledge plugin — interview for its scope, scaffold the repo, and refine the scope statement that routes future knowledge to it.
disable-model-invocation: true
argument-hint: "[plugin-name]"
---

Create a knowledge plugin the router can actually use. The scope statement is the routing prompt — invest in it.

1. Interview the user briefly (2–4 questions): What products/systems/processes does this plugin cover? What explicitly does NOT belong? Who maintains it (owner/team)? How sensitive is it (`public`, `internal`, `restricted`) and should contributions flow by pull request (`pr`, teams) or direct commit (`commit`, personal)?
2. Compose a 2–5 sentence scope statement from their answers — specific names, not generalities.
3. Run `mneme new <name> --description "<scope statement>" --owner "<owner>" --sensitivity <s> --mode <m>` (binary at `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`).
4. Open the generated `MNEME.md`, refine the "What belongs here / What does NOT belong here" sections with the interview specifics, and show the user the final scope statement.
5. Report the repo path and remind them: add a git remote and the plugin distributes itself — consumers run one `marketplace add` and inherit every merged update.
