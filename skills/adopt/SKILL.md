---
name: adopt
description: Retrofit mneme onto a registered repo — draft its scope statement from what the repo already says about itself, agree it with the user, then add the governance files that are missing. Works on a knowledge plugin and on an ordinary app or service repo, which keeps its knowledge in mneme-index/ and is never turned into a plugin.
disable-model-invocation: true
argument-hint: "[plugin-name]"
---

The binary is `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`.

1. If the repo is not yet registered, register it first: `mneme registry add <name> --repo <url> --clone` (or `--path` for an existing checkout).

2. Read what the repo already says about itself: `mneme adopt $ARGUMENTS --describe` prints a JSON bundle — the README's first paragraph, package manifests, the top-level tree, the language mix, recent commit subjects, which agent-facing docs exist, the scopes already registered, and the mode adoption will pick. It reads and reports; it adopts nothing. Read all of it before you say anything to the user.

3. **Draft the scope statement, then ask.** Do not ask the user to invent one cold — that is how vague scopes get written, and the scope statement is the routing prompt every candidate fact is matched against, so a vague one steals candidates from every sibling scope. Propose a statement built from the bundle, naming which source each claim came from, and describe **what knowledge belongs here** — the systems, failure modes and operational surfaces someone working in this repo learns the hard way. Not what the product is: a README is marketing, and marketing prose used as a routing prompt matches everything.

4. Say where the scope **ends**. When `siblings` is non-empty, state which kinds of knowledge go to each sibling instead ("you already have `team-kb` covering the widget platform; a deploy gotcha specific to THIS service goes here, a platform-wide one goes there"). That boundary is the part the user can actually correct and the part they cannot supply unprompted. Then ask only what the bundle cannot answer: the exclusions, the sensitivity, and any boundary you could not settle yourself. Never re-ask something already in the bundle.

5. Apply the agreed scope: `mneme adopt $ARGUMENTS --description "<the agreed scope>" --owner "<their team>"`.

6. Report what happened. The first line names the **mode** and why it was chosen:
   - **plugin** — the repo is, or is becoming, a knowledge plugin. Full scaffold: manifests, `skills/knowledge-index/`, repo-wide CODEOWNERS and CI.
   - **plain** — an ordinary app, service or infra repo. mneme keeps to `mneme-index/` at the root and takes nothing else over: no plugin manifests, no claim on the repo's own `skills/` or `CONTRIBUTING.md`, CODEOWNERS scoped to `/mneme-index/`, and CI (`mneme-validate.yml`) that only runs when the knowledge changes. Tell the user plainly what plain mode does NOT give them: no marketplace distribution, and no `/mneme:classify` — a plain repo has no destination skills to file facts into. `/mneme:share` and `/mneme:review` work exactly as they do anywhere.

   Override the classification with `--as-plugin` or `--plain` if the user wants the other one.

7. Relay every note the command printed — a CODEOWNERS it found and deliberately left alone comes with the line to add by hand — surface any lint warning about existing content, and remind the user the changes are uncommitted: they review and commit through their repo's normal process.
