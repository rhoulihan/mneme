# Plan 13 — adopt any repo, not only a plugin

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development`
> or `superpowers:executing-plans`. Steps use `- [ ]` for tracking.

**Goal:** mneme can register, capture into, and deliver PRs to an ordinary repo — an app,
a service, an infra repo — without turning it into a Claude Code plugin.

**Architecture:** a repo is in one of two modes. A **plugin** keeps today's layout
(`skills/knowledge-index/`). A **plain repo** keeps its knowledge in `mneme-index/` at the
root — the same router-plus-facts unit, under a name that cannot collide with an app's own
`skills/` tree and is not tied to one harness. `MNEME.md` at the root remains the scope
statement and the marker that makes a repo registerable, in both modes.

**Decisions taken (Rick, 2026-08-14), not open:**

- Layout for a plain repo is `mneme-index/SKILL.md` + `mneme-index/facts/<topic>.md`.
- Facts persist only in local staging until a share; publication to a repo is always a PR.
- `review` works in plain-repo mode — it accepts/merges PRs and the facts within them.
- `classify` is a **no-op** in plain-repo mode. A plain repo has no destination skills, so
  the librarian pass has nowhere to file anything. It becomes available only if the user
  supplies tooling that defines destinations.

## Context

`registry add` never checked for plugin-ness, so a plain repo can be registered today —
and then bricks on its first new fact topic. Reproduced: `harvest._regenerate_index`
returns early when `.claude-plugin/plugin.json` is absent, so no router `SKILL.md` is
written, and `lint.lint_repo` then walks `skills/` and errors `MN001 SKILL.md not found`,
aborting and rolling the harvest back. That defect is fixed as task 1 and is the reason
this plan starts there rather than with the interview.

Requiring a plugin makes the cheapest and most frequent case the hardest one: a team that
wants its own repo to accumulate what it learns while working in it.

**Known trade-off, accepted:** nothing auto-reads `mneme-index/`. Claude Code discovers
`.claude/skills/` in a plain repo and a top-level directory is inert, so in-repo retrieval
for a teammate without mneme is via the PR and the file tree, not automatic context. mneme
users reach it through the local index as always. Revisit with a generated pointer surface
(`AGENTS.md` stanza) if that proves too thin — deliberately not in this plan.

**Naming note:** `mneme-index/` is a repo directory; `bin/mneme-index` is the standalone
indexer binary. Same words, different things. Docs must not blur them.

## Global constraints

Everything Plan 12 and earlier established still holds in both modes: PR-only (mneme never
writes a target's `main`), the machine gate, the human gate at `/mneme:share`, the secret
scan over what the push ships, and the fact-preservation gate. Adding a mode may not create
a rail where a gate does not run.

---

### Task 1: the knowledge root is a resolution, and a plain repo stops bricking

**Files:** `core/mneme_core/units.py`, `core/mneme_core/harvest.py`,
`core/mneme_core/lint.py`; `tests/core/test_plain_repo_harvest.py` (new)

**Interfaces:**
- `units.PLAIN_ROOT = "mneme-index"`; `units.FACTS_PLAIN = "mneme-index/facts"`.
- `units.is_plugin(root: Path) -> bool` — `.claude-plugin/plugin.json` exists.
- `units.knowledge_root(root: Path) -> Path` — `skills/knowledge-index` for a plugin,
  `mneme-index` otherwise. Resolution, not a write rule.
- `units.facts_write_dir(root)` returns `mneme-index/facts` in plain mode. The docstring's
  existing rule — writes never follow an existing legacy layout — is unchanged; mode is
  not layout drift.
- `facts_dir`, `facts_dirs`, `fact_files`, `find_fact_file` read `mneme-index/facts` too.
- `harvest._regenerate_index` keys on `knowledge_root`, never on `plugin.json`, and ALWAYS
  writes the router.
- `lint.lint_repo` lints the knowledge root's `SKILL.md`, and in plain mode does not walk
  the repo's own `skills/` at all.

- [x] **Steps:** failing test first (register a plain repo, stage one fact, `apply_batch`
  succeeds and puts `mneme-index/facts/<topic>.md` + `mneme-index/SKILL.md` on the branch
  with `main` untouched) → implement → full suite + `bin/mneme lint .` → mutation-verify
  each new branch → commit.

---

### Task 2: `adopt` in plain-repo mode

**Files:** `core/mneme_core/scaffold.py`, `core/mneme_core/cli.py`, `skills/adopt/SKILL.md`;
`tests/core/test_adopt_plain_repo.py` (new)

**Interfaces:** on a non-plugin repo, `mneme adopt` writes `MNEME.md`, `mneme-index/SKILL.md`,
`mneme-index/facts/.gitkeep`, `CONTRIBUTING.md` and a CODEOWNERS line for the knowledge
root — and does **not** write `plugin.json`, `marketplace.json` or `release.yml`.
`validate.yml` is offered, path-scoped to the knowledge root, never repo-wide. Manifests
become an explicit opt-in (`--as-plugin`) rather than the default imposition.

- [x] **Steps:** failing test → implement → verify the adopted repo lints clean and a
  harvest into it succeeds → commit.

---

### Task 3: the scope interview for a repo that has no scope

**Files:** `skills/adopt/SKILL.md`, `core/mneme_core/cli.py` (a `--describe` helper that
prints what the agent should read); `tests/core/test_adopt_plain_repo.py`

**Interfaces:** propose-and-correct, never interview from nothing. The skill reads the
README's first paragraph, the package manifest name/description, the top-level tree, the
language mix, recent `git log` subjects, and any existing `AGENTS.md`/`CLAUDE.md`; drafts a
scope statement naming its sources; then asks only what is not derivable — the exclusions,
the sensitivity, and the boundary against already-registered scopes ("you already have
`team-kb` covering the widget platform; where does a deploy gotcha for THIS service go?").

The composer must describe *what knowledge belongs here*, not what the product is: a README
is marketing, and marketing prose as a routing prompt steals candidates from every sibling
scope.

- [ ] **Steps:** the skill contract first, then a test that the CLI helper surfaces every
  input the skill claims to read → commit.

---

### Task 4: `classify` declines plain-repo mode

**Files:** `core/mneme_core/classify.py`, `skills/classify/SKILL.md`;
`tests/core/test_classify_rails.py`

**Interfaces:** `classify begin` in plain-repo mode raises a MnemeError naming why —
there are no destination skills to file facts into — and pointing at `review` and `share`,
which do work. `review` is unaffected in both modes. `migrate` stays available (a plain
repo can still carry a legacy `facts/`).

- [ ] **Steps:** failing test → implement → confirm `review`/`share`/`migrate` still work
  on a plain repo → commit.

---

### Task 5: status, detection and docs

**Files:** `core/mneme_core/cli.py`, `docs/getting-started.md`, `docs/install.md`,
`README.md`, `docs/superpowers/specs/2026-08-11-mneme-design.md`

**Interfaces:** `mneme status` and `mneme registry list` show each plugin's mode.
Registration of an already-adopted repo asks nothing it can read from `MNEME.md`. The
getting-started guide gains a plain-repo path with real transcripts, and says plainly what
plain mode does NOT give you (no marketplace distribution, no classify, no automatic
in-repo context for a teammate without mneme).

- [ ] **Steps:** implement → capture real transcripts → commit → release.

## Verification

1. A plain repo: register → capture → share → PR → merge, end to end, with `main` never
   written by mneme.
2. The same repo lints clean, indexes, and is searchable.
3. `classify` declines with a message that names the reason and the alternatives.
4. `review` accepts a PR into a plain repo and extracts its facts.
5. A plugin repo behaves exactly as it does today — every Plan 12 test still green.
6. Every gate (secret scan over the push range, preservation, lint) runs in both modes.

## Out of scope

Generated pointer surfaces (`AGENTS.md` stanza, `.claude/skills/`), nested per-directory
scopes, promotion of a plain repo into a plugin, and any new distribution mechanism.
