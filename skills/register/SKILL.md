---
name: register
description: Register an existing knowledge repo with mneme — from a git URL you have access to (mneme clones it for you) or a local checkout — so its knowledge becomes searchable and it can receive harvested candidates.
disable-model-invocation: true
argument-hint: "[plugin-name] [git-url-or-path]"
---

Register an existing repo as a knowledge plugin. The binary is `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`.

1. Determine the source from the arguments or by asking: a git URL (GitHub, GitHub Enterprise, GitLab — anything the user can clone) or an existing local checkout.
2. Ask for sensitivity (`public`/`internal`/`restricted`) and contribution mode (`pr` for shared repos, `commit` for personal) if not obvious; defaults are `internal`/`pr`.
3. For a URL: run `mneme registry add <name> --repo <url> --clone [--sensitivity S] [--mode M]`. For a local checkout: run `mneme registry add <name> --repo <url-or-origin> --path <checkout> [...]`.
4. Check the repo's routing readiness: if its `MNEME.md` scope statement is missing (`mneme context` shows "(no scope statement)"), say so and offer `/mneme:adopt <name>` to retrofit governance — without a scope statement the distiller cannot route knowledge to this plugin.
5. Offer to make it searchable now: `mneme index rebuild` (or `mneme db enable` if the index was never enabled).
