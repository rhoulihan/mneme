# Changelog

All notable changes to the mneme engine. The engine ships as a single versioned
unit: the distribution (`pyproject.toml`), the plugin manifest
(`.claude-plugin/plugin.json`), and both packages (`mneme_core.__version__`,
`mneme_index.__version__`) always carry the same version, and
`tests/e2e/test_release.py` enforces it. `mneme-index` is standalone by import
boundary, not by release cadence — it is not independently versioned. Knowledge
plugins scaffolded by `mneme new` do carry their own independent versions.

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
