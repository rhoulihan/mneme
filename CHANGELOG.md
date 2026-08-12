# Changelog

All notable changes to the mneme engine. The engine ships as a single versioned
unit: the distribution (`pyproject.toml`), the plugin manifest
(`.claude-plugin/plugin.json`), and both packages (`mneme_core.__version__`,
`mneme_index.__version__`) always carry the same version, and
`tests/e2e/test_release.py` enforces it. `mneme-index` is standalone by import
boundary, not by release cadence — it is not independently versioned. Knowledge
plugins scaffolded by `mneme new` do carry their own independent versions.

## 0.5.0 — 2026-08-12

- **Facts live under the router skill** — the canonical fact location is
  `skills/knowledge-index/facts/`, so the generated index skill and the files it
  routes to travel as one self-contained directory. Every consumer (scaffold,
  adopt, regenerate, lint, index build, harvest, verify) resolves the location
  through one rule, `units.facts_dir`: canonical when present, legacy top-level
  `facts/` when that is what a repo has, canonical when creating. **Legacy repos
  keep working unchanged** — nothing is migrated behind your back — and unit ids
  stay `facts/<stem>#<topic-key>` in both layouts, so dedup, the declined
  ledger, and `similar-to` continuity survive the move.
- **`/mneme:classify` — the librarian pass** — a prompt-driven triage of the
  facts a repo has accumulated from accepted PRs, run on the repo you are
  standing in (the current directory is the argument; it must resolve to a
  registered knowledge plugin or the command says exactly that). Deterministic
  rails wrap the judgment: `mneme classify begin` (clean-tree preconditions plus
  a `mneme/classify-*` branch), `mneme classify prepare` (a JSON bundle of every
  fact, every candidate destination skill, and the librarian contract), the
  agent proposes the complete mapping and **waits for your approval** before
  editing anything, then `mneme classify finalize` migrates any legacy facts
  with `git mv`, regenerates the knowledge-index, gates on lint and a secret
  scan over changed files, commits with provenance, and opens the pull request.
  `mneme classify abort` restores the tree and deletes the branch; any failure
  inside finalize rolls back the same way.
- **Knowledge is never deleted by a classify pass** — every fact either lands in
  a skill's content (with its meaning, tags, and verified date preserved) or
  stays a fact. The reorganization is delivered as a PR like every other
  contribution: classify commits only on its own branch, never on `main`.
- **Persisted detection declines** — declining to register a detected knowledge
  repo is now recorded in `detection-declined.jsonl` (`mneme detection decline`,
  `mneme detection list`), so the session-start nudge never asks about that repo
  again — across sessions and compactions. Previously the decline was
  instruction-only and did not survive the session.

## 0.4.0 — 2026-08-12

- **PR-only contributions** — the `pr | commit` registry mode is gone; mneme
  never writes a registered repo's `main`. Every harvest lands on a
  `mneme/harvest-*` branch (pushed with a PR when a remote exists, left local
  otherwise), enforced by an invariant test that main never advances. Legacy
  registries carrying the old `mode` key load cleanly and shed it on save.
- **Harvest writes stay inside the target repo** — skill names and fact topics
  come from candidate frontmatter (model-generated text) and were joined straight
  into the write path, so a name of `../../kb-b/skills/injected` wrote into a
  *sibling* registered repo's working tree and `../../../loose` wrote outside
  every repo, where the harvest's own rollback could not reach it. Both are now
  refused: the name must be kebab-case and the resolved path must stay under
  `<repo>/skills` or `<repo>/facts`.

## 0.3.0 — 2026-08-12

- **Session-start knowledge-repo detection** — opening a session inside an
  unregistered repo that carries a `MNEME.md` makes the injected brief ask the
  user whether to register it with the local mneme (origin URL pre-filled,
  `/mneme:adopt` offered when governance files are missing, declines
  respected). Detection is deterministic (`routing.find_knowledge_repo`) and
  can never break session start.

## 0.2.1 — 2026-08-12

- **Fixed scaffolded secret-scan workflow** — `validate.yml` shipped with
  literal `$$f`/`$$rc` (string.Template escapes in a constant that is written
  raw, never rendered), which bash expanded as the shell PID: every scan
  received a garbage filename and the step crashed on `exit $rc`. First
  observed as a CI failure on the mneme-dev-knowledge repo. Scaffold templates
  written without rendering now carry plain `$`, with a regression test
  asserting no written scaffold file contains `$$`.

## 0.2.0 — 2026-08-12

The first version that closes the loop end to end: notice → flag → distill →
machine gate → human gate → pull request → team inheritance.

### Added

- **Retrieval (Phase 02)** — `mneme-index`, a standalone SQLite FTS5 hybrid
  search component over any tree of `SKILL.md`/fact files, plus `mneme search`
  and the read-only `mneme db query` surface. Harness-neutral by design: it
  imports only unit parsing and errors from the core.
- **Scaffold factory (Phase 03)** — `mneme new` generates a knowledge repo that
  is simultaneously a valid plugin, its own single-plugin marketplace, and a
  governed commons: manifests, `MNEME.md` scope statement, CODEOWNERS,
  CONTRIBUTING with the promotion rule, and lint/scan/version-bump CI. Index
  schema v2 adds summaries; `mneme db enable/disable` controls the local index.
- **Distiller machine gate (Phase 04)** — routing scopes read from registered
  `MNEME.md` files, sensitivity boundaries that stop candidates drifting toward
  less-restricted repos, the session noticing brief (`mneme context`), and the
  two-phase `mneme distill prepare` / `mneme distill ingest` gate: the model
  returns structured proposals only, and tested code validates, renders
  canonically, secret-scans, dedups, routes, and stages.
- **Harvest and pull requests (Phase 05)** — `mneme share list/diff/apply` and
  `mneme decline` as the human gate; git plumbing that commits units with `Mneme-Source:`
  provenance trailers, pushes harvest branches, and opens PRs through `gh` with
  graceful degradation to commit mode; `mneme verify` staleness sweep;
  `mneme registry add --clone` and `mneme adopt` for repos that already exist.
- **Claude Code adapter (Phase 06)** — the engine ships as a plugin and its own
  marketplace: SessionStart hook injecting the noticing brief, Stop/PreCompact
  hooks running the background distiller, the `/mneme:*` command skills
  (new, register, adopt, capture, share, status, verify), and a model-invocable
  retrieval skill so the agent searches installed knowledge before reinventing it.
- **Dogfood and release (Phase 07)** — an end-to-end test that drives the whole
  loop through the real `bin/` launchers and hook scripts against a scratch home,
  a shimmed `claude`, and a local bare remote; CI running pytest on Python 3.10
  and 3.12 plus `bin/mneme lint .` (the engine's skills pass the engine's own
  linter); `docs/dogfood/seed-proposals.json`, nine real lessons from building
  mneme that clear the real ingest gate with zero quarantines; CONTRIBUTING and
  SECURITY policies for the public repo.

### Hardened (audit-driven)

- **Semantic candidate hashing** — dedup and the declined ledger hash the
  semantic content of a unit, stripping mneme's own stamps (verified dates,
  session labels), so declined items stay declined instead of resurfacing with a
  fresh hash the next day.
- **SELECT-only `db query`** — the query surface rejects anything but a single
  read statement, closing the write/attach escape from an ostensibly read-only
  command.
- **SQLite URI encoding** — index paths are percent-encoded before being built
  into `file:` URIs; previously a `#`, `?`, or `%` in a path silently voided the
  `mode=ro` guarantee and could open a different database.
- **Line-granular fact edits** — `apply_fact` originally rewrote whole fact
  files through a frontmatter round-trip, a whole-file rewrite masquerading as a
  delta edit that could reformat files mneme did not create; it now splices
  exactly one bullet line, byte-preserving everything else including BOMs and
  CRLF endings.
- **Atomic harvest rollback** — a harvest that fails at any point (apply, lint
  gate, re-scan, commit, or push) resets the knowledge repo to a clean `main`,
  deletes the abandoned harvest branch, and leaves every candidate staged, so
  the identical harvest can simply be retried instead of wedging the next one on
  a dirty working tree.
- Additional fixes from the same audit layer: staging-store frontmatter
  injection, JSON-escaped scaffold template substitution, size caps on every
  untrusted proposal field, and usage-error exit codes on `mneme-index`.

## 0.1.0 — 2026-08-11

- **Foundation (Phase 01)** — `~/.mneme` state layout, the plugin registry,
  one-line flag capture, the canonical skill and fact unit formats, the staging
  store with quarantine and a declined ledger, the secret scanner, the schema
  linter, and the `mneme` CLI they all hang from. Standard library only,
  Python ≥ 3.10.
