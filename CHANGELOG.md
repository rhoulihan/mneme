# Changelog

All notable changes to the mneme engine. The engine ships as a single versioned
unit: the distribution (`pyproject.toml`), the plugin manifest
(`.claude-plugin/plugin.json`), and both packages (`mneme_core.__version__`,
`mneme_index.__version__`) always carry the same version, and
`tests/e2e/test_release.py` enforces it. `mneme-index` is standalone by import
boundary, not by release cadence — it is not independently versioned. Knowledge
plugins scaffolded by `mneme new` do carry their own independent versions.

## 0.7.0 — 2026-08-13

Facts writes are unconditional and a legacy layout is migrated, never accommodated
(user direction, 2026-08-12). Before this release `units.facts_dir` fell back to a
top-level `facts/` whenever the canonical directory was absent, so a repo scaffolded
before v0.5.0 — mneme's own development-knowledge plugin is exactly that repo — kept
receiving new facts at its root forever. "Respect the existing layout" turned out to be
the mechanism that made the old layout permanent.

- **Every new fact topic lands canonically** — `units.facts_write_dir` is always
  `skills/knowledge-index/facts/`, whatever exists on disk. A topic file that already
  exists in a legacy repo is still appended to where it lives, because splitting one
  topic across two files would give one unit id two homes; only a topic file that does
  not exist yet is created, and it is created canonically. Reads are untouched:
  `facts_dir`, `facts_dirs`, `fact_files` and `find_fact_file` still resolve both
  layouts, so an unmigrated repo stays fully readable by lint, verify, search and the
  index.
- **`layout.migrate_legacy_facts` — a git-aware move that never loses a bullet** — a
  tracked file moves with `git mv` so `git log --follow` still reaches its pre-move
  history; an untracked one is renamed. When the same topic exists in both layouts the
  legacy file's bullets are *merged* into the canonical one — topic-key dedup, canonical
  wins a collision, and a bullet that will not parse is carried over verbatim rather than
  dropped. Every destination goes through the same containment proof a harvest write
  does, because a legacy filename is repo content and therefore untrusted; a `facts/`
  symlink is refused rather than followed, since the migration's own `git rm` would
  otherwise delete files at the far end of the link.
- **Migration happens inside every branch flow, automatically** — `share apply` runs it
  as the first thing on the harvest branch, before any candidate is applied, and the
  shared classify/review finalize rail runs the same function. The order is what makes
  the rest correct by construction: the index regeneration that follows reads the moved
  files through `fact_files`, so the router skill's paths are right for the new location
  without a special case. The moves are recorded under a `Migrated:` section of the
  commit body, bounded so a mature pre-0.5 repo's several hundred files cannot produce a
  body that `git commit -m` refuses (E2BIG) or that silently costs the pull request its
  description. PR-only holds throughout — migration only ever happens on a `mneme/*`
  branch, and any failure rolls back to a clean `main` with the branch deleted.
- **`mneme migrate`** — the same rail with no session in the middle, for the repo whose
  only pending change *is* the migration: nothing staged, nothing to classify. Branch
  `mneme/migrate-*`, migrate, regenerate the index, lint + secret scan the changed files,
  commit, push and open the PR when there is a remote. A repo with nothing to migrate
  gets `no legacy facts directory — nothing to migrate` and no leftover branch.
- **The repos that need it are the ones nobody is contributing to, so `mneme status` says
  so** — one `legacy facts layout: <name> (run: mneme migrate in that repo)` line per
  registered plugin whose clone still carries a root `facts/`, silent when there are
  none, and silent about a clone that has gone missing. `mneme adopt` prints the same
  notice and seeds the canonical directory, having previously left a pre-0.5 repo with
  nowhere canonical to write — which is precisely how the next fact re-confirmed the
  legacy layout.
- **Triage accuracy** (carried from the Plan 11 audit) — a fact whose sentence already
  appears in a skill's prose is labelled `already-integrated` from the files themselves,
  with no database involved, ranking above the index-derived `possibly-integrated` hint;
  the generated router skill is excluded, so a fact cannot be "integrated" into the
  listing of itself. Declines are plugin-scoped, so knowledge one repo's maintainer
  rejected is not reported as declined for another (ledger lines written before the field
  existed stay global). `gitops.list_open_prs` reports truncation instead of quietly
  triaging the first hundred pull requests, and the bundle carries the clone's `head`
  with `behind_remote`, because a "new" label computed against a stale tree can be wrong
  and the maintainer should be told which it is.
- **Injection and traversal hardening** (also carried) — the standing rule that everything
  quoted is DATA is now emitted *before* the untrusted content in the triage bundle, the
  classify bundle and the distiller prompt, and repeated after it: a defense serialized
  after what it governs is read second. `review._header_path` rejects backslash segments
  and NUL alongside its POSIX checks, so a fabricated diff path can never reach a caller
  looking clean. The spec's command inventory is checked against `skills/` in both
  directions by a test, so it can neither undercount the surface nor invent one.

## 0.6.1 — 2026-08-13

Two defects found by a real harvest into a registered knowledge repo, plus the
getting-started guide. Both defects had the same shape: mneme generated content
that mneme's own machine gate then rejected, so every harvest needed a manual
repair before its pull request could merge.

- **`MAX_DESCRIPTION` is 500, not 1024** — Claude Code rejects a `SKILL.md` whose
  frontmatter `description` exceeds 500 characters, and mneme's gate allowed twice
  that, with independent copies of the wrong number in `lint`, `compose` and
  `proposals`. That is the worst way for a gate to fail: lint passes, CI passes,
  the pull request merges, and the plugin is broken at install time for everyone
  who pulls it. Observed at 854 characters on one repo and 560 on another. The
  limit now lives once, in `units.MAX_DESCRIPTION`.
- **The generated knowledge-index description is O(1) in fact count** — it used to
  spell out `Topics: a, b, c…`, one entry per fact file, so any budget was a cliff
  the repo walked off as it grew. It now reports a count; measured at 461
  characters with 0 topics and 463 with 500. The topic NAMES stay in the body
  table, which no reader loads until the skill is opened. Trimming is
  word-boundary aware with an ellipsis — the old cap was a bare slice that could
  sever the final token and leave a half-written topic name that routes nowhere
  while the description still read as complete. The `DISTILLER_PROMPT` no longer
  instructs the model to emit descriptions in the band the gate now rejects.
- **The secret scanner stopped blocking mneme's own topic slugs** — a fact file's
  `topic:` frontmatter reads to the generic entropy rule as an assignment, and a
  descriptive slug clears the 4.0 bar (`mongodb-java-driver-tls-trust-not-configurable-via-uri`, 54 characters, entropy 4.016). Length is not a usable
  discriminator: entropy is not monotonic in it, so a 60-character slug passes
  while a 45-character one blocks. The exemption is scoped to the LINE — `topic:`
  or `name:`, kebab-case value, nothing else on it.
  A wider first attempt exempted any lowercase-hyphenated value under any key and
  opened a real hole: diceware, 1Password and Bitwarden passphrases are exactly
  that shape, and `\b` does not fire across an underscore, so `db_password`,
  `client_secret` and `PGPASSWORD` never reach the keyword-anchored rule either.
  236 of 280 realistic passphrase assignments went from blocked to clean; the
  key-scoped version returns 0 of 280. A known pre-existing gap — the keyword rule
  cannot see underscored names — is recorded in the tests rather than closed here.
- **`docs/getting-started.md`** — a worked walkthrough: create / register / adopt,
  then one piece of knowledge from the moment you learn it to a merged pull
  request. Every transcript is captured from a real run. Linked from the README,
  which also gains a corrected scaffold file tree.

## 0.6.0 — 2026-08-12

- **`/mneme:review` — inbound PR triage** — the maintainer side of the loop, run
  on the repo you are standing in (the current directory is the argument, same
  resolution and failure message as classify). `mneme review triage` lists every
  open pull request through `gh` and annotates every fact bullet each one ADDS:
  `duplicate` (the bullet says what the repo — or an earlier-listed PR — already
  says, cross-PR dedup included, or it collides with an existing unit id),
  `declined` (a human already rejected that knowledge; the ledger remembers),
  `possibly-integrated` (the index's nearest existing unit, named for you to
  judge), or `new`. Identity is the bullet's **text**: the `[category]` prefix,
  the `#tags`, and the `verified:` stamp are all contributor-controlled, so
  keying on the rendered line let a one-character retag re-surface knowledge a
  human had already declined.
  Diff parsing is read-only and tolerant: malformed bullets, non-fact files,
  CRLF, and binary noise never crash a triage — unparseable additions come back
  in a per-PR `skipped` list, and added `skills/*/SKILL.md` files are surfaced
  for human eyes rather than dedup'd.
- **A PR that deletes knowledge is surfaced too** — triage was addition-only, so
  a pull request removing forty fact bullets produced an empty annotation set and
  read as clean. Each PR now carries a `removed` list (with `moved` marking a
  bullet re-added elsewhere in the same PR), and the maintainer contract says a
  deletion needs a reason before a merge is recommended.
- **A PR's content can never pose as a PR's structure** — a diff renders added
  lines with a `+` prefix, so a file whose content is `++ b/<path>` arrives as
  `+++ b/<path>`. Triage now walks hunks (`@@ -a,b +c,d @@`) instead of scanning
  line by line: inside a hunk nothing is a file header, so a pull request can no
  longer attribute fabricated fact bullets or skill additions to files it never
  touched — nor poison cross-PR dedup so an honest later PR looks duplicate.
  Bullet lines are also length-capped, and the bullet grammar is linear rather
  than quadratic, so no single PR line can stall the command.
- **Labels are evidence; the human decides** — the agent proposes exactly one
  verdict per PR (merge / close-as-duplicate naming the covering unit ids /
  extract-new-facts) and executes nothing without your explicit approval for
  **that** pull request. No mneme code path ever runs `gh pr merge` or
  `gh pr close`: remote mutations are agent actions gated on your approval, not
  rails. There is no batch approval and no default yes.
- **Extraction rides the classify rails** — `mneme review begin` /
  `finalize` / `abort` are the same deterministic rails classify uses,
  generalized to a branch prefix: new facts worth keeping are written on a
  `mneme/review-*` branch, knowledge-index regenerated, lint and secret scan
  gated over changed files, committed with provenance and delivered as mneme's
  own pull request. The triage bundle names the write destination (`fact_files`
  for a topic that exists, `facts_dir` for a new one) so extraction follows the
  repo's own fact layout, and a repo whose two layouts carry the same filename is
  refused *before* finalize touches anything — the approved work stays on the
  branch to be fixed instead of being rolled back away. Classify and review share
  the active-branch guard, so the two passes can never interleave on one repo,
  and `main` is still never written.
- **Facts can no longer vanish at finalize** — "never delete knowledge" was
  enforced only by instruction prose, and a pass that deleted a fact file
  without integrating its content used to finalize successfully. Both rails now
  compute the fact bullets on `main` before committing and require each one to
  be accounted for on the branch — still a parseable bullet in a file the readers
  actually sweep, or its text carried verbatim into a skill file the branch
  changed. The generated `skills/knowledge-index/` (which is where the canonical
  facts directory lives) is not an integration destination, so a bullet cannot be
  de-bulleted into prose in place, or hidden in a `facts/archive/` subdirectory,
  and still count as preserved. Unaccounted bullets fail the finalize with each
  lost line named, and the existing rollback restores the repo.
- **Untrusted content is framed as data** — the classify bundle, the review
  bundle, and the distiller prompt all carry verbatim repo, staging, and PR text
  (skill descriptions, fact bullets, PR titles) inside an instruction context.
  All three templates now state the standing rule that this material is DATA
  from untrusted contributors and that imperative text inside it is content to
  classify, never commands to obey.
- **`gh` is now a runtime requirement for review only** — it was already
  required to open pull requests; review additionally needs it to read them, and
  says so plainly instead of failing obscurely when it is missing.

## 0.5.0 — 2026-08-12

- **Facts live under the router skill** — the canonical fact location is
  `skills/knowledge-index/facts/`, so the generated index skill and the files it
  routes to travel as one self-contained directory. Writes pick one destination
  (`units.facts_dir`: canonical when present, legacy top-level `facts/` when that
  is what a repo has, canonical when creating); every reader (regenerate, lint,
  index build, verify, classify) sweeps `units.fact_files` — **both** layouts —
  so a repo mid-migration never has facts that are committed but unsearchable.
  **Legacy repos keep working unchanged** — nothing is migrated behind your back
  — and unit ids
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
