# Backlog: `mneme new --no-plugin` — a knowledge repo that is not a plugin

**Status:** raised 2026-08-23 (Rick). Implement after Plan 14 (re-routing) lands.

## The ask

`mneme new <name>` always scaffolds a Claude Code plugin: `.claude-plugin/plugin.json`,
`marketplace.json`, a release workflow that bumps the version on every merge. Some teams
want a **generic knowledge repo** — a repo whose purpose IS knowledge, governed the same
way, but not published as a plugin and not carrying manifests nobody will install from.

## The design question this raises, which is not obvious

v0.8.1 gave a registered repo a MODE, and the rule is `units.maintains_skills`: **mneme owns
`skills/` exactly when its own router lives inside it.** That has a consequence here.

A naive `--no-plugin` would scaffold the plain layout — `mneme-index/` at the root — because
that is what `knowledge_root` returns for a repo with no manifest. But that would be wrong
for this ask:

- `maintains_skills` would be False, so mneme would not lint the repo's skills,
- `/mneme:classify` would refuse (no destination skills to file facts into),
- `harvest.apply_skill` would refuse, so the repo could hold facts and never skills.

A repo whose *purpose* is knowledge wants all three. Plain mode was designed for an
application repo where `skills/` belongs to the app — a completely different situation.

**So `--no-plugin` most likely means: the canonical `skills/knowledge-index/` layout, with
skills, WITHOUT the plugin manifests and the release workflow.** That is exactly the
population `units.established_root` was built for — the hand-built knowledge repo that was
never packaged — and `tests/core/test_unpackaged_knowledge_repo.py` already pins that it
works: `knowledge_root` follows the established root, `maintains_skills` is True, lint
enforces, classify runs, skill candidates apply.

If that reading is right, the feature is mostly scaffolding subtraction rather than a new
mode, and the mode machinery already supports it.

## What to decide before building

1. **Confirm the reading above with Rick.** "Not a plugin" could mean "plain mode like an app
   repo" instead, and the two produce very different repos. The distinguishing question:
   *should this repo be able to hold skills that mneme lints and classify files facts into?*
   Yes → canonical layout without manifests. No → plain `mneme-index/`.
2. **Distribution story.** Without `marketplace.json` there is no `/plugin install`. Teams
   consume it by cloning and registering, or by mneme's own index. Say so in the scaffold's
   README rather than leaving a reader to discover it.
3. **`release.yml`** bumps a version inside `plugin.json`. With no manifest it must not be
   written (the same rule `_plain_files` already applies).
4. **Promotion.** A `--no-plugin` repo that later wants distribution needs `mneme adopt
   <name> --as-plugin` to add the manifests without moving the knowledge root. Worth an
   explicit test: adopting `--as-plugin` over an established `skills/knowledge-index/` must
   NOT create a second root — `_adopt_mode`'s forked-root refusal should already cover it,
   since the established root is already the plugin one.
5. **`validate.yml`** should still be written — the format gates matter more here, not less,
   since there is no marketplace install to fail loudly.

## Sketch

`scaffold.create(home, name, *, as_plugin: bool = True)`, with `cli` growing
`mneme new <name> --no-plugin`. The file map is today's minus `.claude-plugin/*` and
`release.yml`; everything else — `MNEME.md`, `CONTRIBUTING.md`, `CODEOWNERS`, `AGENTS.md`,
`README.md`, `.gitignore`, `validate.yml`, `skills/knowledge-index/SKILL.md`,
`skills/knowledge-index/facts/.gitkeep` — is unchanged. `/mneme:new` should ask, or take the
flag, and the getting-started guide gains the third path.

Related: `docs/superpowers/plans/2026-08-14-plan-13-any-repo.md`,
`docs/superpowers/backlog/2026-08-14-any-repo-adoption.md`.
