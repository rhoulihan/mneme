---
name: new
description: Create a new governed knowledge repo — interview for its scope, scaffold it, and refine the scope statement that routes future knowledge to it. Ships as an installable plugin by default, or with --no-plugin as a repo that is governed and searchable but not distributed.
disable-model-invocation: true
argument-hint: "[plugin-name]"
---

Create a knowledge plugin the router can actually use. The scope statement is the routing prompt — invest in it.

1. Interview the user briefly (2–4 questions): What products/systems/processes does this plugin cover? What explicitly does NOT belong? Who maintains it (owner/team)? How sensitive is it (`public`, `internal`, `restricted`)?
   Ask whether anyone outside the team will INSTALL it. If not — a team's own knowledge, an internal ops commons — offer `--no-plugin`: same layout, same skills, same facts, same gates, minus the plugin manifests and the release workflow. It stays fully lintable, classifiable and searchable; it just is not published to a marketplace. Do not offer it as "lighter" — nothing is missing except distribution, and `mneme adopt <name> --as-plugin` adds that later without moving any knowledge.
2. Compose a 2–5 sentence scope statement from their answers — specific names, not generalities.
3. Run `mneme new <name> --description "<scope statement>" --owner "<owner>" --sensitivity <s>` (binary at `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`), adding `--no-plugin` if that is what they chose.
4. Open the generated `MNEME.md`, refine the "What belongs here / What does NOT belong here" sections with the interview specifics, and show the user the final scope statement.
5. Report the repo path, and relay the command's own `no-plugin:` lines verbatim when they appear. For a plugin, remind them: add a git remote and it distributes itself — consumers run one `marketplace add` and inherit every merged update, and harvested knowledge arrives as a pull request off a `mneme/harvest-*` branch. Until there is a remote, harvests stay on that local branch for them to merge; mneme never commits to `main` either way.
