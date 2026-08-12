# Mneme — Design Specification

**Date:** 2026-08-11 · **Status:** approved pending final review · **Authors:** Rick Houlihan, Claude
**Prior art:** [docs/research/2026-08-11-prior-art.md](../../research/2026-08-11-prior-art.md)

## 1. Vision

**The plugin is the memory. Memories are skills.**

Mneme is a knowledge-mining engine for AI coding agents. As a user works, mneme notices hard-won knowledge — procedures that succeeded after dead ends, non-obvious constraints, institutional facts — and decomposes it into skills and fact files. Those units flow through a machine gate and a human gate into **knowledge plugins**: git repositories that are simultaneously installable agent plugins and governed knowledge commons. Anyone granted access installs the plugin and inherits everything merged; every future merge arrives through normal plugin updates.

Companies, universities, and government agencies get repositories of institutional knowledge — products, codebases, processes, procedures, implementations — that their AI tools consume natively and their existing git governance (PRs, CODEOWNERS, branch protection, audit) controls completely. No vendor SaaS anywhere in the loop.

Don't tell the AI to document what it did. Tell it to decompose the knowledge it's mining into a collection of skills anyone in the organization can install.

## 2. Positioning

Research (see prior-art report) confirmed the target intersection is empty as of 2026-08: no shipped system combines passive capture during normal agent work → local staging → user-curated review → PR into an open git knowledge repo → team inheritance via plugin updates. Closest analogs: Anthropic's Claude Tag skills-repo loop (Slack-only, skills-only, prompted, no staging), Devin Knowledge (proprietary store, one-click publish, no git/review), claude-mem-sync (raw observations, per-project), Hivemind (ungated, proprietary backend). An open, unanswered Anthropic feature request (anthropics/claude-code#38536) asks for nearly this design.

Three research findings drive the architecture:

1. **Distribution is already solved.** Marketplace repos with version bumps, SHA pinning, and org sync deliver "teammates inherit updates" natively. Mneme builds zero sync infrastructure.
2. **Review throughput kills knowledge commons, not noise.** (anthropics/skills: 762 open PRs, unabsorbed; tldr-pages thrives on rigid schema + lint-in-CI + small-N approvals.) Mneme therefore scaffolds the full governance pipeline into every knowledge repo; machines settle format so humans judge only substance.
3. **Never let agents bulk-rewrite a knowledge store.** Context collapse and memory poisoning are the documented killers; mitigations are delta-only edits to individually-addressable units and a human merge gate — which git provides natively.

## 3. Locked decisions

| Decision | Choice |
|---|---|
| Knowledge unit | Two-tier: procedural → skills (SKILL.md), declarative → fact files |
| Contribution flow | Curated harvest: local staging, explicit `/mneme:share` human gate, then PR. No auto-push, ever. **Revised 2026-08-12: PR-only; the `pr` \| `commit` mode split removed by user direction — mneme never writes a registered repo's `main`** |
| Engine/content separation | Mneme's repo is pure tooling; knowledge lives in separate repos that are themselves installable plugins |
| Registry + routing | User registers any number of knowledge plugins; mneme creates, maintains, and routes captured units to each |
| Capture architecture | Distiller: in-session flagging + background distillation at Stop and PreCompact |
| Platforms | Anthropic (Claude Code) and OpenAI (Codex). Harness-neutral core, thin adapters. Claude Code adapter first; Codex adapter v1.1 |
| Local DB | Optional derived layer: hybrid retrieval index + queryable fact store. SQLite v1 behind a storage-driver interface. Files always canonical |
| Indexer | Standalone tool-agnostic component (`mneme-index`) |

## 4. System architecture

### 4.1 Components

**The mneme engine plugin** (this repo — tooling only, never knowledge):

- `core/` — harness-neutral logic:
  - `bin/mneme` — deterministic CLI: registry ops, flag capture, staging management, schema lint, secret/PII scan, routing support, index management, git/branch/PR plumbing, scaffold generation. The LLM decides *what*; the CLI does *how* — every mechanical operation reproducible and auditable.
  - `mneme-index/` — standalone indexer module (§6.3).
  - Prompts and skills as markdown (portable).
- `skills/` — behavioral skills: *noticing* (flag golden paths as they happen), *harvesting* (drives the share/review flow), *scaffolding* (creates knowledge plugins), *retrieval* (teaches the agent to use `mneme search` for vague-notion lookup when the DB layer is enabled).
- `commands/` — `/mneme:capture`, `/mneme:share`, `/mneme:new`, `/mneme:register`, `/mneme:adopt`, `/mneme:status`, `/mneme:verify`, `/mneme:classify` (§7.7).
- `agents/` — the distiller subagent definition (separate role from the working agent: generate ≠ reflect ≠ curate).
- Harness adapters:
  - **Claude Code adapter (v1):** `.claude-plugin/plugin.json`, `hooks/hooks.json` (SessionStart, Stop, PreCompact), command and skill wiring.
  - **Codex adapter (v1.1):** maps the same core to Codex's configuration/hook surface; Codex's existing local SQLite usage makes the index layer native there.

**Local state** (machine-local, never committed): default `~/.mneme/`, overridable via `MNEME_HOME`:

```
~/.mneme/
├── registry.json      # registered knowledge plugins — source of truth
├── staging/           # candidate units awaiting harvest
│   └── quarantine/    # candidates with secret-scan hits (redaction required)
├── declined.jsonl     # rejected-candidate ledger (anti-re-proposal)
├── repos/             # local clones of registered knowledge repos
├── mneme.db           # optional derived DB (index + fact store); rebuildable
└── logs/
```

**Knowledge plugins** — one git repo each, created by `/mneme:new` or attached by `/mneme:register`. Each repo is simultaneously a valid plugin and its own single-plugin marketplace, so consumers run one `marketplace add` and inherit every merged update through native plugin mechanics.

### 4.2 Registry

`registry.json` entries: `name`, `repo` (git URL), `path` (local clone), `scope` (pointer to the repo's MNEME.md), `sensitivity` (`public` | `internal` | `restricted`), optional capture exclusions. The registry is a flat file so it works with the DB layer off, stays human-auditable, and survives DB rebuilds. When the DB layer is enabled the registry mirrors into it for joins; derivation is strictly one-way (files → DB). Entries written before 2026-08-12 may carry a `mode` key (and any other unknown key): loading ignores it, and the next save drops it.

**Contributions are PR-only** (revised 2026-08-12 by user direction; the earlier `mode` field selected `pr` or `commit` per repo and is gone). There is no per-repo contribution setting: every harvest lands on a `mneme/harvest-*` branch, pushed with a PR when the repo has a remote and left local for the human to merge or push when it does not. Personal repos are not an exception — the branch costs nothing and keeps the same reviewable artifact and provenance trail that shared repos get.

### 4.3 Routing

Each knowledge repo carries `MNEME.md` at its root: a scope statement ("what belongs here / what does not"), sensitivity level, and curation rubric. The distiller classifies every candidate against the registered scope statements — registering a plugin automatically teaches the router. Ambiguous candidates stage as *unassigned* and are resolved by the user at harvest. Routing never moves a candidate toward a less-restricted repo than its source context without explicit user override at harvest.

## 5. Knowledge repo anatomy and unit formats

### 5.1 Scaffold (`/mneme:new <name>`)

```
<name>/
├── .claude-plugin/
│   ├── plugin.json            # version bumped on every merge → consumers update
│   └── marketplace.json       # self-referential single-plugin marketplace
├── .codex-plugin/             # Codex packaging (multi-harness, superpowers pattern)
├── AGENTS.md                  # cross-tool entry point
├── MNEME.md                   # scope + sensitivity + curation rubric = routing prompt
├── skills/
│   ├── <skill-name>/
│   │   ├── SKILL.md           # agentskills.io-valid procedural unit
│   │   └── references/        # supporting files (router-tree targets)
│   └── knowledge-index/       # generated router skill exposing the facts tier
│       └── facts/
│           └── <topic>.md     # declarative units, one topic per file
├── CODEOWNERS                 # reviewer routing per knowledge area
├── CONTRIBUTING.md            # rubric for humans + agents; anti-slop policy
├── .github/workflows/
│   ├── validate.yml           # mneme lint, secret scan, similarity warn, link check
│   └── release.yml            # auto version bump on merge to main
└── README.md
```

The scaffold output must pass `claude plugin validate` and its own generated CI on day one.

**Facts location (revised 2026-08-12 by user direction).** Facts live *inside* the router skill at `skills/knowledge-index/facts/`; the generated index and the files it routes to are one self-contained directory that travels together. Repos scaffolded before this revision keep a top-level `facts/` and are read exactly as before. Two resolution rules, both in `units`, and no consumer hardcodes a path: **writes** ask `facts_dir` (canonical when it exists, else legacy when *that* exists, else canonical for creation) so a repo's layout is never forked by an append; **reads** — lint, verify, index build, index regeneration, the classify bundle — sweep `fact_files`, which covers *both* layouts. The asymmetry is the point: a repo that carries both (a 0.5 scaffold plus a hand-filed top-level fact) must never hold knowledge that is committed and on disk yet invisible to retrieval. No repo is migrated behind its owner's back. `/mneme:classify` (§7.7) performs the migration deliberately, as a reviewable PR.

### 5.2 Procedural units — skills

Spec-valid Agent Skills (`skills/<name>/SKILL.md`, portable to every adopting tool):

```yaml
---
name: deploy-widget-service          # lowercase-hyphen, matches directory
description: >                       # trigger-rich, ≤1024 chars — reviewed as
  Use when deploying widget-service…  # carefully as the body: description
                                     # quality IS retrieval quality
license: <repo license>
metadata:
  mneme-type: skill
  mneme-source: <repo>@<session-ref>
  mneme-captured: 2026-08-11
  mneme-last-verified: 2026-08-11
  mneme-supersedes: <unit id, optional>
---
```

Body requirements (the promotion rule, enforced at the machine gate): the verified procedure, and the named failure pattern — what went wrong before the fix, which dead ends were eliminated. Knowledge enters only with evidence of success and a reason it was non-obvious.

Within-plugin retrieval is the **SKILL.md router tree**: brief SKILL.md files route down to `references/` files, so a plugin can carry large reference datasets without context bloat, deterministically, on any locked-down machine.

### 5.3 Declarative units — facts

`skills/knowledge-index/facts/<topic>.md` (revised 2026-08-12 — canonical location, see §5.1; legacy top-level `facts/<topic>.md` is read unchanged): YAML frontmatter (`topic`, `modified`), body of typed observation bullets:

```markdown
- [constraint] Staging DB resets nightly at 04:00 UTC #staging (verified: 2026-08-11)
- [gotcha] v2 API silently truncates batch writes over 500 items #api (verified: 2026-08-11)
```

Categories: `decision | constraint | gotcha | runbook-note | reference`. Each bullet is individually addressable — its unit id is `facts/<stem>#<normalized-topic-key>` **regardless of the physical location**, with a content hash for change detection (the same id scheme the index and dedup gate use). Holding ids stable across the 2026-08-12 move is what lets dedup, the declined ledger, and `similar-to` continuity survive it: the same bullet has the same id in either layout. **Delta edits only** — agents never regenerate a file (context-collapse guard). Topic-keying makes every update a reviewable diff against an existing bullet rather than a near-duplicate.

Facts reach consumers through the generated `knowledge-index` router skill (progressive disclosure: trigger metadata always loaded, topic list on activation, fact files on demand). The index skill is regenerated mechanically by `bin/mneme`, never by an LLM.

## 6. Optional local database layer

Off by default; enabled per machine with `mneme db enable`. Files remain canonical always; the DB is derived and rebuildable (`mneme index rebuild`). The DB can never block anything — absence or corruption degrades to file routing.

### 6.1 Role 1 — hybrid retrieval index

Every skill and fact bullet across all registered and installed knowledge plugins is indexed into one local SQLite file: unit id, name, description, triggers, body summary, tags, source plugin, path, content hash. Search: FTS5 out of the box; sqlite-vec plus a local embedding model as an opt-in vector layer; reciprocal-rank fusion when both are enabled. This solves cross-plugin discovery at scale — "find me a skill vaguely matching this" over hundreds of units returns top-k entry points; the SKILL.md router trees take over from there. The same index powers the machine gate's dedup/similarity checks and `/mneme:verify` sweeps.

Design intent: the deterministic router tree is the correct within-plugin architecture; the index is the cross-plugin discovery layer. They compose — the index finds the door, the routers walk the halls.

### 6.2 Role 2 — queryable fact/reference store

Fact bullets land in structured tables (query by type, tag, topic, plugin). Knowledge-plugin SKILL.md files may call `mneme db query` to retrieve reference data as an alternative to file-tree routing when datasets are large or relational.

### 6.3 `mneme-index` — standalone component

A tool-agnostic module: point it at any directory tree of SKILL.md/fact files → SQLite index + search CLI (`mneme-index build <dir>`, `mneme-index search <query>`). Mneme consumes it via `bin/mneme` and the retrieval skill; any other harness (Codex first) imports the same component rather than reimplementing.

### 6.4 Engine strategy

SQLite in v1 (FTS5 default; vector opt-in), behind a thin storage-driver interface (open/index/search/query/rebuild) specced so enterprises can later point the layer at Oracle 26ai (native vector + JSON, converged) or Postgres. Interface on paper, one implementation in code.

## 7. The pipeline

### 7.1 Capture (during work — near-zero overhead)

SessionStart injects the noticing instructions plus a one-line registry summary (targets and scopes). When a golden path emerges, the working agent flags it in one line — `mneme flag "<what + why non-obvious>"` — a cheap CLI append; no distillation mid-session. `/mneme:capture "<note>"` is the explicit equivalent. **Capture exclusions are enforced at the source:** repos/paths/topics marked never-learn (global config or per-registry) suppress noticing entirely for that scope, so exclusion actually binds the capture layer.

### 7.2 Distill (background — Stop and PreCompact)

Triggered when the session has flags (or `capture.distill = always`), at session end **and before compaction** — PreCompact matters because the full transcript is still intact before detail is summarized away. The distiller is a detached headless agent (separate role) reading the transcript, the flags, and native auto-memory. It extracts typed candidates and runs the **machine gate**:

1. **Promotion rule** — verified success + named failure pattern + non-obviousness (not derivable from public docs).
2. **Secret/PII scan** — deterministic (`mneme scan`); hits quarantine the candidate.
3. **Dedup** — against existing knowledge in registered clones *and* already-staged candidates (indexed similarity when the DB layer is on; grep otherwise). An update to an existing unit becomes a delta against that unit.
4. **Routing** — classification against registered MNEME.md scopes; ambiguous → unassigned.

Output: candidate markdown in `~/.mneme/staging/` with id, type (`skill` | `fact`), edit kind (`new` | `update` — updates carry the target unit id and are expressed as deltas), target plugin, confidence, routing rationale, and provenance. Nothing has left the machine. Successive runs (PreCompact then Stop) top up rather than duplicate.

### 7.3 Harvest (`/mneme:share` — the human gate)

A review queue grouped by target plugin: new units shown whole; updates shown as diffs against the existing unit. Per candidate: approve, edit, reject, or re-route. Rejections land in the **declined ledger** so the distiller never re-proposes them. For each target with approvals, `bin/mneme` executes one pipeline, with no per-repo variant: sync main (read-only — the last time main is touched), fresh branch (`mneme/harvest-<utc-timestamp>`), apply delta edits, regenerate router/index skills, run the repo's own lint + scan locally (fail before the PR, not in it), commit with provenance trailers **on the branch**, then push the branch and open the PR via `gh` when the repo has a remote — otherwise leave the branch local and report `no remote — branch left local; merge it or add a remote and push`. Either way the clone is returned to an unchanged `main` with the branch preserved: mneme hands the contribution over and never merges it. A failure anywhere after the branch is created rolls the clone back to the pre-harvest `main` and leaves the candidates staged (§9).

**Invariant (revised 2026-08-12):** no mneme code path checks out `main` to write knowledge, commits knowledge on `main`, or pushes `main`. The direct-to-main path that `mode: commit` repos used is removed, and a test asserts both local and remote `main` are byte-identical before and after a harvest.

### 7.4 Review and merge (repo side)

Scaffolded CI re-validates format, secrets, similarity, links; CODEOWNERS routes reviewers; reviewers judge substance only. Merge triggers the version-bump workflow — accepted knowledge is immediately installable.

### 7.5 Inherit and maintain

Consumers add the marketplace once; native plugin updates deliver everything merged. `/mneme:status` surfaces staged candidates, quarantined items, open PRs, available knowledge updates, and index freshness. Updates trigger incremental index rebuilds when the DB layer is on. `/mneme:verify <plugin>` runs the staleness sweep: units whose `mneme-last-verified` exceeds policy are re-verified and a maintenance PR proposes updates or retirements.

### 7.6 Correction loop

When installed knowledge proves wrong or stale mid-session, the noticing skill flags it as a knowledge issue; the distiller emits a **correction candidate** routed to the owning plugin. Corrections travel the same gated pipeline as new knowledge. Numeric helpful/harmful telemetry is deferred (§12).

### 7.7 Classify (`/mneme:classify` — the librarian pass, added 2026-08-12)

Harvest optimizes for the moment of capture: a fact lands as a fact because that is the honest shape of what was learned. Over many accepted PRs a repo therefore accumulates a facts tier that has outgrown itself — bullets that belong *inside* the skill whose work they constrain. Classify is the periodic librarian pass that files them.

**The current directory is the argument.** There is no plugin-name parameter anywhere in the surface: classify resolves the repo from the working directory via `routing.plugin_for_path` (any subdirectory works) and fails with a single clear instruction when that directory is not inside a registered knowledge plugin.

Classification is LLM judgment over repo structures that vary, so it is prompt-driven **by design**, wrapped in deterministic rails:

1. **`mneme classify begin`** — preconditions (registered plugin, git repo, clean tree, no classify branch already active), read-only `main` sync, then a fresh `mneme/classify-<utc-timestamp>` branch. Every edit happens there.
2. **`mneme classify prepare`** — a JSON bundle: every fact (file, line, category, text, tags, verified date, unit id), every candidate destination skill (name, description, directory, file listing, `knowledge-index` excluded), whether a legacy layout is still in use, and the librarian contract itself.
3. **The human gate** — the in-session agent proposes the *complete* mapping to the user (fact → destination skill and section, facts staying put, facts merely restating what a skill already says, any new skill several related facts justify) and **waits for approval** before editing anything. Automatic classification without that approval is explicitly out of scope.
4. **Apply** — ordinary delta edits in the working tree, preserving each fact's meaning, tags, and verified date and keeping each skill's existing structure.
5. **`mneme classify finalize`** — `git mv` of any remaining legacy facts into `skills/knowledge-index/facts/`, mechanical knowledge-index regeneration, the **fact-preservation gate** below, then the same gates a harvest passes: `mneme lint` error-free and a secret scan blocker-free over every file changed on the branch. Commits with provenance on the branch, pushes and opens the PR when a remote exists, and returns the clone to an unchanged `main`.
6. **`mneme classify abort`** — restores the tree, returns to `main`, deletes the branch. Any failure inside finalize performs the same rollback automatically (§9); nothing to classify is likewise a clean rollback, not a wedged branch.

**Never delete knowledge — enforced, not asked (added 2026-08-12).** Every fact either lands in a skill's content or remains a fact. Prose said so from the start; finalize now proves it. Before the commit, the shared rail computes every fact bullet present on `main` and requires each to be **accounted for** on the branch: still a bullet in some fact file, or its normalized text appearing verbatim inside a skill file the branch changed (the integration case, which is also better provenance — the instructions tell the agent to carry the original sentence across). Anything unaccounted for fails the finalize, naming each lost bullet, and takes the automatic rollback. The check is a floor, not a diff-quality judge: a reworded integration that drops the original sentence fails it. Both rails share the gate, so §7.8 extraction inherits it. The §7.3 invariant holds unchanged: classify writes only its own branch, never `main`, and the reorganization reaches the repo the same way every other contribution does — as a pull request a human merges.

### 7.8 Review (`/mneme:review` — inbound PR triage, added 2026-08-12)

Harvest and classify are the contributor side. Review is the maintainer side: the knowledge repo has open pull requests from people and agents across the org, and the expensive question about each is not format — CI settles that — but whether what it adds is knowledge the repo does not already hold. **The current directory is the argument**, resolved exactly like classify (§7.7), and `gh` is required: triage reads pull requests, and without the CLI the command says so instead of degrading.

1. **`mneme review triage`** — read-only. Lists open PRs (`gh pr list`), reads each diff (`gh pr diff`), and parses the fact bullets each one ADDS from the unified diff: added lines in files under a facts directory (canonical or legacy). Parsing is deliberately tolerant — malformed bullets, non-fact files, CRLF, and binary noise never crash a triage; unparseable additions come back in a per-PR `skipped` list, and added `skills/<name>/SKILL.md` paths are surfaced in `skills_added` for human reading rather than machine dedup.
2. **Machine annotation** — every parsed addition is labelled from evidence the engine already computes: `duplicate` (its `units.semantic_hash` matches an existing bullet in the repo, or a bullet an earlier-listed PR adds — cross-PR collisions included), `declined` (the declined ledger already carries that semantic content, so a human has rejected it), `possibly-integrated` (the index's nearest existing unit, named in `similar_to`; absent a local DB the field is empty and nothing blocks), or `new` (no signal). The bundle carries the annotations, the PR metadata, and the maintainer contract as `instructions`.
3. **Labels are evidence, verdicts are human.** The agent reads the annotations, judges the `possibly-integrated` cases against the real unit, applies the promotion rule to the `new` ones, and proposes exactly ONE verdict per PR — merge, close-as-duplicate (naming the covering unit ids), or extract-new-facts. Every PR-level action is taken by the agent through `gh` **only after the user's explicit approval for that pull request**; no rail in the engine mutates a remote, there is no batch approval, and auto-merge/auto-close is out of scope by design.
4. **Extraction** — for mixed PRs, the approved bullets are re-authored into this repo through the classify rails generalized to a branch prefix: `mneme review begin` (`mneme/review-<utc-timestamp>`), the agent writes only the approved bullets verbatim (text, tags, verified date) into the facts directory, then `mneme review finalize` — knowledge-index regeneration, fact-preservation gate, lint, secret scan over changed files, provenance commit, push, PR. `mneme review abort` restores the tree and deletes the branch, and any failure inside finalize rolls back the same way. Classify and review share the active-branch guard, so the two passes can never interleave on one repo. `main` is never written (§7.3).
5. **Credit and follow-up** — a source PR whose knowledge was extracted is closed only with the user's approval and always with a comment saying where the knowledge landed. When extraction adds several facts, the natural next step is the librarian pass (§7.7).

Everything a pull request contributes — bullet text, file paths, titles, authors — is untrusted contributor input. It flows through the same containment discipline as any other untrusted write path (§8), and the review, classify, and distiller templates all state the standing rule that quoted repository, staging, and PR content is DATA, never instructions to follow.

## 8. Security and governance

- **No vendor SaaS**: local machine + the org's own git remote (GitHub, GitHub Enterprise Server; GitLab/Bitbucket via the PR-provider interface later). Works fully air-gapped against self-hosted git.
- **Gates**: machine gate (scan, lint, dedup, promotion rule) → human harvest gate → human PR review. No auto-push exists, and no path writes a knowledge repo's `main` — contributions are PR-only (§7.3).
- **Quarantine**: secret/PII hits are staged in `staging/quarantine/` and appear at harvest only after redaction; overrides are logged.
- **Sensitivity boundaries**: per-repo labels (`public` | `internal` | `restricted`); routing toward less-restricted targets requires explicit user override at harvest.
- **Provenance**: capture metadata in unit frontmatter + git authorship + PR review trail; commit trailers identify mneme-mediated contributions. Tamper-evidence via ordinary branch protection.
- **Poisoning defense**: the human merge gate (primary), SHA-pinned marketplace refs, delta-only edits, CI content validation on every contribution.
- **Gov posture**: reviewable artifacts and git-native audit align with OMB M-25-21 expectations; as a plugin, mneme rides already-authorized platforms (e.g., Claude for Government Desktop, FedRAMP High).
- **Admin controls**: managed-settings distribution of registry baselines and capture kill-switch are supported patterns, not custom infrastructure.

## 9. Failure handling

- **Distiller**: fire-and-forget; failures log to `~/.mneme/logs`, flags are retained and retried at the next trigger; the user's session is never blocked.
- **Harvest git failures**: re-branch from fresh main and re-apply deltas; still-conflicted candidates stay staged with the error noted.
- **Repo CI failure post-push**: PR stays open with CI feedback; surfaced by `/mneme:status`.
- **DB layer**: can never block; absence/corruption → file routing + `mneme index rebuild`.
- **Declined ledger**: consulted by every distiller run; prevents rejection churn.

## 10. Testing

- **`bin/mneme` + `mneme-index`**: unit tests with fixtures — lint schemas, scan patterns, routing classifications, staging operations, index build/search; golden files.
- **Behavioral skills**: superpowers-style per-skill pressure tests on subagents.
- **End-to-end**: scripted transcript → distill → stage → harvest against a local bare repo → assert PR contents.
- **Scaffold**: generated repos must pass `claude plugin validate` and their own CI.

## 11. v1 scope

**Ships:** engine plugin with Claude Code adapter; `bin/mneme`; `mneme-index` (FTS5); scaffold factory; distiller + harvest flow; registry + routing; staleness sweep; correction loop; dogfood knowledge repo (mneme's own development knowledge, captured by mneme while building mneme).

**Deferred:** Codex adapter (v1.1, with Patrick Meredith); vector layer (opt-in module after FTS5 proves out); Oracle 26ai / Postgres drivers (interface specced only); GitLab/Bitbucket PR providers (interface specced, `gh` only); numeric usage telemetry; cross-org federation tooling; MCP surface.

## 12. Risks

1. **Anthropic generalizes Claude Tag's skills-repo loop to Claude Code.** Hedges: facts + skills, local curated staging, multi-plugin registry + routing, open governance pipeline, dual-platform, tool-agnostic format.
2. **Native team-memory sync ships** (unverified leak): would be ambient KV without curation — mneme positions as the governed tier above it.
3. **Proprietary competitors add git export + review** (Devin, Augment): open format + BYO-git remains the structural moat.
4. **Review-throughput collapse in real deployments**: the reviewer experience is the product — machine-settled format, batched shares, CODEOWNERS routing, and (as policy knobs in CONTRIBUTING.md) trust tiers and time-decay approval fallbacks.
5. **Distillation cost/noise**: flags-only default trigger, declined ledger, and promotion rule keep candidate volume proportional to genuinely hard-won knowledge.
