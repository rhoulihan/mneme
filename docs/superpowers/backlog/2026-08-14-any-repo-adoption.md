# Backlog: adopt any repo, not only a plugin

**Status:** researching (design not started). Raised 2026-08-14.

## The ask

mneme should be able to register and submit facts to **any repo, not just knowledge
plugins**. When `/mneme:adopt` runs on a repo that is not a plugin and has no skills
defining its scope, mneme should interview the user and generate a **scope specification**
for that repo, which is then used to capture and route facts to it.

The target is an ordinary application, service, infrastructure or docs repo: the team wants
the knowledge mneme captures while they work on it to land *in that repo*, near the code it
is about, without turning the repo into a Claude Code plugin.

## Why it matters

Today every knowledge target must be a plugin. That is the right shape for a knowledge
commons someone installs, and the wrong shape for the far more common case — a team that
just wants its own repo to accumulate the constraints and gotchas discovered while working
in it. Requiring a plugin makes the cheapest, highest-frequency case the hardest one.

## What has to be decided (not yet decided)

- **Layout.** Reuse `skills/knowledge-index/facts/` without the plugin manifests? A hidden
  `.mneme/`? A visible `knowledge/`? Whichever it is, a plain repo should be promotable
  into a full plugin later without moving files.
- **The scope spec.** Today it is prose in `MNEME.md`, and only that section is read for
  routing. Much of it is derivable from the repo itself — README, languages, tree, history,
  an existing `AGENTS.md`/`CLAUDE.md`. Propose-and-correct is probably better than
  interviewing from nothing, but auto-derived scope has its own failure modes.
- **Retrieval.** A plugin distributes itself; consumers install it and inherit merged
  updates. A plain repo does not. How is this knowledge found, by the team and by anyone
  outside it?
- **Which invariants assume plugin structure.** Lint expects `SKILL.md` shapes; the index
  build walks `skills/`; `classify` reorganizes facts into skills that may not exist;
  `mneme verify` skips `knowledge-index`. Each rail needs an answer.

## Constraints that do not change

PR-only: mneme still never writes a target repo's `main`. The machine gate and the human
gate at `/mneme:share` still apply. Whatever the layout, the secret scan and the
fact-preservation gate must cover it.

## Next step

Research report commissioned 2026-08-14 (prior art, layout options, scope-spec design,
retrieval, risks, and 2–4 whole-design options). **Discuss options before implementing** —
explicitly requested.
