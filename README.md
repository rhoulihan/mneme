# mneme

<p align="center">
  <img src="assets/mneme.png" alt="mneme — knowledge retention engine" width="640">
</p>

**The plugin is the memory. Memories are skills.**

Mneme (after the Greek muse of memory) is a knowledge-mining engine for AI coding agents. As you work, it notices hard-won knowledge — the fix that landed after three dead ends, the constraint nobody wrote down, the procedure that actually works — and decomposes it into **skills** and **facts**. Those units flow through a machine gate and a human gate into **knowledge plugins**: git repositories that are simultaneously installable agent plugins and governed knowledge commons. Anyone you grant access installs the plugin and inherits everything merged; every future merge arrives through a normal plugin update.

Companies, universities, and government agencies get repositories of institutional knowledge — products, codebases, processes, procedures, implementations — that their AI tools consume natively and their existing git governance (pull requests, CODEOWNERS, branch protection, audit history) controls completely. **No vendor SaaS anywhere in the loop.**

Don't tell the AI to document what it did. Tell it to decompose the knowledge it's mining into a collection of skills anyone in the organization can install.

## Why this doesn't already exist

We [surveyed the landscape](docs/research/2026-08-11-prior-art.md) before writing a line of code. As of mid-2026, every ecosystem has exactly one half of the loop:

- **Manual git-shared knowledge, no capture** — Cursor rules, Copilot instructions, Amp skills, CLAUDE.md/AGENTS.md files, every awesome-list commons. Communities already share agent knowledge via pull requests; they just write all of it by hand.
- **Auto-capture into a proprietary store, weak or no review, no federation** — Devin Knowledge, Augment Cosmos, Windsurf Memories, hosted memory platforms. One user's click (or no click at all) publishes to everyone, in a database you don't own.

Nobody ships **capture → local staging → user-curated review → PR into an open git repo → team inheritance via plugin updates**. The "promotion gate with a review queue" is documented in 2026 practitioner literature as the best practice teams must hand-build. Mneme is that missing intersection, built on three findings from the research:

1. **Distribution is already solved.** A knowledge repo that is also a plugin marketplace inherits versioned, SHA-pinned, org-syncable distribution for free. Mneme builds zero sync infrastructure.
2. **Review throughput kills knowledge commons, not noise.** Every commons that thrives pairs a rigid unit schema with lint-in-CI so machines settle format and humans judge only substance. Mneme scaffolds that governance into every knowledge repo it creates.
3. **Never let agents bulk-rewrite a knowledge store.** Context collapse and memory poisoning are the documented failure modes; the mitigations are delta-only edits to individually-addressable units and a human merge gate — which git provides natively.

## The loop

<p align="center">
  <img src="assets/the-loop.svg" alt="The mneme loop: work session → background distiller → machine gate → staging → human gate (/mneme:share) → pull request → merge → consumers inherit → back to the next work session" width="960">
</p>

Nothing leaves your machine without passing a deterministic machine gate **and** your explicit approval. Nothing enters a shared repo without a human merge. There is no auto-push mode — by design, not by configuration.

## What a knowledge plugin looks like

`mneme new acme-knowledge` generates a repo that is a valid plugin, its own single-plugin marketplace, and a governed commons on day one:

```
acme-knowledge/
├── .claude-plugin/
│   ├── plugin.json            # version bumped on every merge → consumers update
│   └── marketplace.json       # self-referential marketplace: one `add`, updates forever
├── MNEME.md                   # scope statement + sensitivity — doubles as the routing prompt
├── skills/
│   ├── <skill-name>/SKILL.md  # procedural units (Agent Skills format, portable to ~40 tools)
│   └── knowledge-index/       # mechanically regenerated router over the facts tier
├── facts/
│   └── <topic>.md             # typed, tagged, dated fact bullets — delta-edited, never rewritten
├── CODEOWNERS                 # reviewer routing per knowledge area
├── CONTRIBUTING.md            # the promotion rule + anti-slop policy
└── .github/workflows/         # lint, secret scan, auto version bump on merge
```

**Skills** carry procedures with their failure patterns — knowledge enters only with evidence of success and the dead ends that made it non-obvious. **Facts** are single-line typed bullets (`decision | constraint | gotcha | runbook-note | reference`) with tags and verified-dates:

```markdown
- [constraint] Staging DB resets nightly at 04:00 UTC #staging (verified: 2026-08-11)
- [gotcha] v2 API silently truncates batch writes over 500 items #api (verified: 2026-08-11)
```

You can register **any number** of knowledge plugins — personal, team, per-product, per-project. Each repo's `MNEME.md` scope statement teaches the router where new knowledge belongs; sensitivity labels (`public | internal | restricted`) keep candidates from drifting toward less-restricted repos without your explicit override.

## Status

Mneme is in active development, built plan-by-plan with strict TDD. Current state:

| Phase | Delivers | State |
|---|---|---|
| 01 — Foundation | State layout, registry, flag capture, unit formats, staging + quarantine + declined ledger, secret scan, schema lint, `mneme` CLI | ✅ merged |
| 02 — Retrieval | `mneme-index`: standalone SQLite FTS5 hybrid search over any skill/fact tree; `mneme search` / `mneme db query` | ✅ merged |
| 03 — Factory | `mneme new` scaffold factory + security hardening (SELECT-only queries, schema v2 with summaries, `db enable/disable`) | ✅ merged |
| 04 — Distiller | Routing scopes, sensitivity boundaries, session noticing brief, two-phase distill machine gate | ✅ merged |
| 05 — Harvest | `/mneme:share` review flow, git/PR plumbing with provenance trailers, staleness sweep, register/adopt for existing repos | ✅ merged |
| 06 — Adapter | Claude Code plugin wiring: hooks, `/mneme:*` commands, behavioral skills, background distiller | ✅ merged |
| 07 — Dogfood | End-to-end harness + mneme's own development-knowledge plugin, captured by mneme | ✅ merged |

Deferred by design: vector search layer (FTS5 first), Oracle 26ai / Postgres storage drivers (interface specced), Codex adapter (the core and `mneme-index` are deliberately harness-neutral), cross-org federation tooling.

## Installing

Mneme installs like any Claude Code plugin — its repo is its own marketplace:

```
/plugin marketplace add rhoulihan/mneme
/plugin install mneme@mneme
```

Requires Python ≥ 3.10 and git on the machine; the engine is standard-library-only.

First run, hook behavior, configuration env vars, and troubleshooting: see [docs/install.md](docs/install.md).

## Using mneme

Everything is a slash command. Behind each one, a deterministic, fully-tested CLI does the mechanical work — that separation is the design: the agent converses, the machine gates.

| Command | What it does |
|---|---|
| `/mneme:new <name>` | Interview for scope, then scaffold a governed knowledge plugin — repo, manifests, CI, routing scope statement |
| `/mneme:register <name> <url>` | Register an existing repo you have access to (clones it for you); offers governance retrofit |
| `/mneme:adopt <name>` | Retrofit mneme governance onto an existing repo — adds only what's missing, never overwrites |
| `/mneme:capture <note>` | Flag hard-won knowledge the moment it happens — one line, distilled in the background later |
| `/mneme:share` | The human gate: review staged candidates (diffs, boundary flags, similarity hints), approve or decline, then commit/PR |
| `/mneme:status` | Pipeline dashboard: plugins, pending flags, staging, submissions, index freshness |
| `/mneme:verify <name>` | Staleness sweep over a knowledge plugin, with guided re-verification |

And mneme rides the session without being asked: a SessionStart hook injects the noticing brief so the agent flags golden paths as they happen, Stop/PreCompact hooks run the background distiller over what was flagged, and a retrieval skill has the agent search installed knowledge by vague notion before reinventing something the organization already knows.

### Under the hood (contributors, CI, scripting)

Every command drives `bin/mneme`, a zero-install stdlib-only CLI you can use directly:

```bash
git clone https://github.com/rhoulihan/mneme && cd mneme
bin/mneme new acme-knowledge --owner your-team        # what /mneme:new runs
bin/mneme registry add team-kb --repo git@github.com:acme/kb.git --clone
bin/mneme search "batch truncation"                   # FTS across registered plugins
bin/mneme lint path/to/any/knowledge/repo             # machines settle format (CI runs this)
printf 'key = AKIA...' | bin/mneme scan -             # secret scan, exit 2 on blockers
python3 -m pytest                                     # test suite (dev-only dependency: pytest)
```

The `mneme-index` component also works standalone against any directory of `SKILL.md`/fact files — that's deliberate: it's the piece other harnesses can adopt directly:

```bash
bin/mneme-index --db /tmp/i.db build path/to/skill-tree
bin/mneme-index --db /tmp/i.db search "vague notion of what I need"
```

## Design principles

- **Files are canonical; everything else is derived.** Knowledge travels as reviewable markdown in git. The local database is an optional index — delete it and rebuild any time. Git gives provenance (`blame`), temporal history (`log`), rollback, and authenticated authorship for free.
- **Machine gate first, human gate second.** Deterministic code validates schema, scans for secrets, deduplicates, and routes — before you ever see a candidate. You review substance, not format.
- **LLM judgment is quarantined to prompts.** The distiller's proposals are untrusted structured data; tested code renders every unit canonically. An LLM never edits the knowledge store directly.
- **Delta edits only.** Skills are unit-granular files; facts are individually-addressable bullets. No agent may regenerate a whole store — the documented context-collapse failure mode.
- **Ride existing trust.** PRs, CODEOWNERS, branch protection, CI, marketplaces: institutions already govern code this way. Mneme makes knowledge flow through the same pipes rather than building parallel ones — which is also why it works air-gapped against self-hosted git.

## Enterprise & government posture

Everything runs locally plus your own git remote. Capture exclusions bind at the source (excluded repos/paths never even generate flags). Secret findings quarantine candidates until redacted. Sensitivity labels enforce distribution boundaries. Provenance is triple-layered: capture metadata in unit frontmatter, git authorship, and the PR review trail — tamper-evident under ordinary branch protection, and a natural fit for audit-driven environments where "reviewable artifacts" beat "ambient memory."

## Repository layout

```
core/mneme_core/    # engine: registry, staging, scan, lint, routing, scaffold, CLI
core/mneme_index/   # standalone retrieval component (imports only units+errors from core)
bin/                # zero-install launchers (mneme, mneme-index) + the background distill pipeline
docs/
├── superpowers/specs/    # the design specification
├── superpowers/plans/    # per-phase implementation plans (full TDD detail)
└── research/             # prior-art survey, platform wiring references
tests/              # pytest suite — one test module per source module
```

## How this repo is built

Mneme is developed spec-first: a [design specification](docs/superpowers/specs/2026-08-11-mneme-design.md) validated against a [prior-art survey](docs/research/2026-08-11-prior-art.md), then executed as a sequence of implementation plans in which every task carries its full test and implementation code. Execution runs as orchestrated agent workflows — implementation agents work under strict test-driven discipline, adversarial reviewers independently re-run suites and diff every commit against the plan, and cross-module auditors sweep each finished branch for the bugs per-task review structurally cannot see. Several real defects in this codebase (a staging-store injection vector, an SQLite URI-parsing hole, template injection into generated manifests) were caught by that audit layer before ever reaching `main`. The process is, deliberately, a preview of what mneme itself exists to support: agents doing the work, machines enforcing format, humans gating what merges.

## License

[Apache-2.0](LICENSE)
