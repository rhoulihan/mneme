# Getting started with mneme

This is a worked walkthrough: create or register a knowledge plugin, then take one piece of
hard-won knowledge from the moment you learn it all the way to a merged pull request.

Every transcript on this page is captured from a real run, with three edits for readability:
absolute paths are shortened to `~/.mneme/...`, the local test remotes are shown as the
GitHub URLs you would really use, and output that needs a live GitHub remote is labelled as
such. Nothing else is touched — the wording, spacing and counts are what the CLI prints.

Already installed? Jump to [choose your starting point](#2-choose-your-starting-point).
Installing, hooks, configuration and troubleshooting live in
[docs/install.md](install.md) — this page assumes you have the plugin installed and picks up
from there.

---

## 1. Before you start

You need Python ≥ 3.10 and git. The engine is standard-library-only — there is nothing to
`pip install`. The `gh` CLI is needed for exactly one command, `/mneme:review`.

**About the `mneme` command.** Claude Code puts the plugin's `bin/` on the `PATH` of its Bash
tool, so inside a Claude Code session `mneme ...` just works — which is where every example
below runs. In your own terminal, or in CI, it will not be on `PATH`; use the full path
instead:

```bash
"$CLAUDE_PLUGIN_ROOT/bin/mneme" --version     # inside a session
~/.claude/plugins/.../mneme/bin/mneme --version   # or wherever the plugin is installed
```

Initialize local state once:

```bash
mneme init
```

```
~/.mneme
```

That directory is yours, not the plugin's: it holds your registry, your staged candidates,
your declined-candidate ledger and (optionally) the search index. It deliberately outlives
any single plugin install.

Everything below is driven by slash commands. Each one runs a deterministic, fully-tested
CLI underneath, and this guide shows both — the slash command because that is the product
surface, and the CLI line because it is what actually runs, and what you would use in CI or
a script.

---

## 2. Choose your starting point

Mneme needs somewhere to put knowledge. There are three ways to get there, and they differ
only in what you already have:

| You have… | Use | What it does |
|---|---|---|
| Nothing yet | **[Path A — create](#3-path-a--create-a-new-knowledge-plugin)** `/mneme:new` | Scaffolds a complete governed repo: plugin manifest, marketplace, CI, CODEOWNERS, scope statement |
| A knowledge repo already (yours or your team's) | **[Path B — register](#4-path-b--register-a-repo-you-already-have)** `/mneme:register` | Points mneme at it so it becomes searchable and can receive harvests |
| A repo that predates mneme — **register it first** | **[Path C — adopt](#5-path-c--adopt-governance-onto-an-existing-repo)** `/mneme:adopt` | Adds the missing governance files, never overwriting what is there |
| An app, service or infra repo — not a knowledge repo at all | **[Path D — adopt a plain repo](#5b-path-d--adopt-an-app-or-service-repo)** `/mneme:adopt` | Adds `mneme-index/` at the root and takes nothing else over |

Paths B, C and D compose, and the order matters: adopt requires the repo to be registered
already, so an existing repo goes through Path B and then Path C or D. You can register
**any number** of plugins — personal, team, per-product, per-service — and mneme routes each
piece of captured knowledge to the right one.

Paths C and D are the same command. `/mneme:adopt` classifies the repo and tells you which
one it picked, so you do not have to choose in advance.

---

## 3. Path A — create a new knowledge plugin

```
/mneme:new acme-knowledge
```

The command interviews you first — briefly, 2–4 questions — because the answers become the
**scope statement**, and the scope statement is the routing prompt. It is what later decides
whether a fact about the billing pipeline belongs in this plugin or another one. Vague scope
statements route badly, so name real products, systems and teams.

It asks what the plugin covers, what explicitly does *not* belong, who maintains it, and how
sensitive it is (`public`, `internal`, `restricted`). Then it runs:

```bash
mneme new acme-knowledge \
  --description "Widget platform operations at Acme: deploy paths, incident runbooks, and the constraints of the billing pipeline. Excludes customer data and anything about the marketing site." \
  --owner acme-platform --sensitivity internal
```

```
created ~/.mneme/repos/acme-knowledge
registered acme-knowledge
```

Two lines, and you have a repo that is three things at once — a valid Claude Code plugin,
its own single-plugin marketplace, and a governed knowledge commons:

```
acme-knowledge/
├── .claude-plugin/
│   ├── marketplace.json
│   └── plugin.json
├── .github/workflows/
│   ├── release.yml
│   └── validate.yml
├── .gitignore
├── AGENTS.md
├── CODEOWNERS
├── CONTRIBUTING.md
├── MNEME.md
├── README.md
└── skills/knowledge-index/
    ├── SKILL.md
    └── facts/
        └── .gitkeep
```

It is already a git repo with one commit on `main`:

```
eaa2196 chore: scaffold acme-knowledge knowledge plugin
```

### The scope statement

`MNEME.md` is the file worth reading before anything else. Here it is exactly as the scaffold
writes it — your `--description` lands in the scope statement, and the two "belongs" sections
are boilerplate at this point:

```markdown
# acme-knowledge — knowledge scope

**Sensitivity:** internal
**Maintainers:** acme-platform

## Scope statement

Widget platform operations at Acme: deploy paths, incident runbooks, and the constraints of the billing pipeline. Excludes customer data and anything about the marketing site.

## What belongs here

- Hard-won procedures (skills): verified fixes, deployment paths, debugging golden paths — each with the failure pattern that made it non-obvious.
- Durable facts: constraints, gotchas, decisions, runbook notes that stay true across tickets.

## What does NOT belong here

- One-off decisions tied to a single ticket or conversation.
- Secrets, credentials, tokens, or personal data — the capture pipeline blocks them, and so does CI.
- Anything derivable from public documentation.

## Routing

This scope statement is the routing prompt: mneme's distiller matches candidate knowledge
against it. Keep it specific — name the products, systems, and processes this plugin covers.
```

Three things about this file are worth knowing before you rely on it:

- **Only the `## Scope statement` section routes.** `## What belongs here` and `## What does
  NOT belong here` are guidance for human contributors — nothing reads them. Exclusions only
  affect routing if you write them *into the scope statement*, as the example above does
  ("Excludes customer data and…"). `/mneme:new` will offer to refine the other two sections
  from your interview answers; that is for the humans.
- **Keep the scope statement on one physical line.** The session brief shows only its first
  line, so a wrapped paragraph gets cut at the first newline. (The distiller still sees the
  whole thing.)
- **Edit it by hand any time — then commit it.** An uncommitted change makes the next
  `share apply` refuse the repo with `has uncommitted changes — commit or stash them first`.
  On a personal repo, commit it on `main`; on a shared repo, send it as a PR like any other
  change.

### The knowledge index

`skills/knowledge-index/SKILL.md` is the router over your facts. It is regenerated
mechanically — never edit it by hand — and starts empty:

```markdown
---
name: knowledge-index
description: "Consult when you need durable facts from acme-knowledge — constraints, gotchas, decisions, and runbook notes. Widget platform operations at Acme: deploy paths, incident runbooks, and the constraints of the billing pipeline. Excludes customer data and anything about the marketing site. Topics listed in this skill route to fact files under facts/."
---

# acme-knowledge fact index

Regenerated mechanically by mneme — do not edit by hand.

| Topic | File | Bullets |
|---|---|---|
```

As facts arrive, mneme adds a row per topic and appends the topic list to the description,
so an agent that has never opened the repo can still tell what is in it.

### Give it a remote

Nothing about mneme requires a remote — but adding one is what turns a personal repo into a
distributable plugin:

```bash
cd ~/.mneme/repos/acme-knowledge
git remote add origin git@github.com:acme/acme-knowledge.git
git push -u origin main
```

Without a remote, harvests still work; the branch simply stays local for you to merge. With
one, mneme pushes the branch and opens a pull request. Either way it **never** writes `main`.

---

## 4. Path B — register a repo you already have

```
/mneme:register team-kb git@github.com:acme/team-kb.git
```

The command asks only for sensitivity (default `internal`) — there is nothing to decide
about contribution flow, because contributions are PR-only by design, not by configuration.

**From a git URL** (mneme clones it for you):

```bash
mneme registry add team-kb --repo git@github.com:acme/team-kb.git --clone --sensitivity internal
```

```
cloned git@github.com:acme/team-kb.git -> ~/.mneme/repos/team-kb
registered team-kb
```

**From a checkout you already have on disk** — use `--path` and skip `--clone`:

```bash
mneme registry add personal-kb --repo git@github.com:you/kb.git --path ~/src/kb
```

```
registered personal-kb
```

Either way, confirm what is registered:

```bash
mneme registry list
```

```
acme-knowledge  internal  local:~/.mneme/repos/acme-knowledge
team-kb  internal  git@github.com:acme/team-kb.git
personal-kb  internal  git@github.com:you/kb.git
```

(Name, sensitivity, repo — two spaces between columns. `local:` marks a plugin with no
remote yet.)

### Is it ready to receive knowledge?

Registering makes a repo *searchable* and *harvestable*, but routing needs a scope
statement. `/mneme:register` checks for one and tells you if it is missing:

```bash
mneme context
```

This prints the noticing brief first (you will meet that in [§6.1](#61-flag-it-in-the-moment))
and then every registered plugin, sorted by name. A plugin with no `MNEME.md` scope shows up
as `(no scope statement)`:

```
## mneme noticing

While you work, flag knowledge worth keeping — do NOT stop to document it.

Flag (one line each, at the moment it happens) when:
- a hard-won fix lands after real dead ends: `mneme flag "<what worked + why it was non-obvious>"`
- installed knowledge proves wrong or stale: `mneme flag --kind knowledge-issue "<what is wrong>"`

Rules: one line per flag; no mid-session distillation (a background distiller runs later);
never flag anything from excluded repos/paths; never include secrets or credentials in flag text.

Registered knowledge plugins:
- acme-knowledge [internal]: Widget platform operations at Acme: deploy paths, incident runbooks, and the constraints of the billing pipeline. Excludes customer data and anything about the marketing site.
- personal-kb [internal]: (no scope statement)
- team-kb [internal]: (no scope statement)
```

Without a scope statement the distiller cannot route knowledge to that plugin — which is what
Path C fixes.

### Sessions notice unregistered repos

Open a session inside a repo that has an `MNEME.md` but is not registered, and mneme asks
you — once — whether to register it. Decline and the answer is persisted
(`mneme detection decline`), so you are never asked about that repo again.

---

## 5. Path C — adopt governance onto an existing repo

`/mneme:adopt` retrofits the governance a knowledge repo needs — scope statement,
contribution rubric, CODEOWNERS, CI — onto a repo that predates mneme. It adds **only what
is missing** and never overwrites a file you already have.

**Register the repo first.** Adopt works on a registered plugin; run Path B on it and then
come back here. Otherwise:

```
mneme: plugin not registered: team-kb
```

```
/mneme:adopt team-kb
```

It asks what the scope should be, then runs:

```bash
mneme adopt team-kb --description "Team knowledge for the shared platform." --owner acme-platform
```

```
mode: plugin — the repo already carries skills/<name>/SKILL.md
added: MNEME.md
added: CONTRIBUTING.md
added: CODEOWNERS
added: .github/workflows/validate.yml
added: .github/workflows/release.yml
added: .claude-plugin/plugin.json
added: .claude-plugin/marketplace.json
added: skills/knowledge-index/SKILL.md
added: skills/knowledge-index/facts/.gitkeep
review and commit these files through your repo's normal process
```

Two things to note.

**It is idempotent.** Run it again and it does nothing:

```
nothing to add
review and commit these files through your repo's normal process
```

**It does not commit.** The files are left in your working tree:

```bash
git status --short
```

```
?? .claude-plugin/
?? .github/
?? CODEOWNERS
?? CONTRIBUTING.md
?? MNEME.md
?? skills/
```

Review them and commit through whatever process your repo normally uses. If the repo's
existing content does not pass mneme's lint, adopt warns rather than failing:

```
warning: existing content has 3 lint error(s) — run: mneme lint ~/.mneme/repos/team-kb
```

**"Never overwrites" cuts both ways.** If the repo already has an `MNEME.md` — even a stub
with just a title and no `## Scope statement` section — adopt leaves it alone, and the plugin
still has no scope statement and still cannot be routed to. Check with `mneme context` after
adopting, and if it still says `(no scope statement)`, add the section by hand.

**The first line is the mode**, and it decides everything below it. Adopt picks `plugin`
when the repo already has a `.claude-plugin/plugin.json` **or** already carries a
`skills/<name>/SKILL.md` — a hand-built knowledge repo that never got packaged is still a
knowledge repo. Anything else is somebody's application and gets
[Path D](#5b-path-d--adopt-an-app-or-service-repo) instead. Override with `--as-plugin` or
`--plain` if it guessed wrong for your repo.

Adopt differs from `new` in three ways: it needs the repo registered first, it writes a
subset of the files (no `AGENTS.md`, `README.md` or `.gitignore`), and it takes sensitivity
from the registry rather than a flag. A repo still using the legacy top-level `facts/`
layout keeps its files exactly where they are — adopt never moves or rewrites content — but
it is seeded with the canonical `skills/knowledge-index/facts/` alongside them, and adopt
says so: the next contribution migrates the old files into it (or run `mneme migrate` in the
repo to do only that).

---

## 5b. Path D — adopt an app or service repo

Most knowledge is learned inside a repo that is not about knowledge at all. You spend a week
finding out that the chargeback webhook replays events for 72 hours, and the place that fact
belongs is the payments service — not a separate knowledge repo somebody has to remember to
open.

`/mneme:adopt` handles that repo too. It does **not** turn it into a Claude Code plugin. It
adds one directory, `mneme-index/`, at the root, and takes nothing else over.

### Register it first, same as Path C

```bash
mneme registry add payments-service \
  --repo git@github.com:acme/payments-service.git \
  --path ~/code/payments-service
```

```
registered payments-service
```

`mneme status` now names the mode of every registered repo:

```
plugins: 1 registered
- payments-service [internal] (plain)
```

### Adopt reads the repo before it asks you anything

The scope statement is the routing prompt — mneme matches every candidate fact against it to
decide which repo the knowledge belongs to — so a vague one quietly steals candidates from
every other scope you have registered. Asking you to write one cold is how vague ones get
written. So `/mneme:adopt` reads what the repo already says about itself first:

```bash
mneme adopt payments-service --describe
```

```json
{
  "repo": {
    "name": "payments-service",
    "mode": "plain",
    "why": "the repo is not a knowledge plugin, so mneme keeps to one directory",
    "knowledge_root": "mneme-index"
  },
  "sources": {
    "readme": "Settles card payments and issues refunds for the widget platform.",
    "manifests": [
      {"file": "pyproject.toml", "name": "payments-service",
       "description": "Card settlement and refunds"}
    ],
    "tree": ["CHANGELOG", "CONTRIBUTING.md", "README.md", "infra", "pyproject.toml", "src", "tests"],
    "languages": {"py": 3, "md": 2, "tf": 1, "toml": 1},
    "recent_subjects": [
      "fix: chargeback webhook replays are not idempotent",
      "fix: retry settlement on gateway 429",
      "initial import"
    ],
    "agent_docs": ["CONTRIBUTING.md"]
  },
  "siblings": []
}
```

It reads and reports; it adopts nothing. The skill drafts a scope from those sources, names
which source each claim came from, and asks you only what they cannot answer — the
exclusions, the sensitivity, and where this scope **ends** relative to the ones you already
have. `siblings` is what makes that last question answerable: if you already have a `team-kb`
covering the widget platform, the draft has to say which knowledge goes there instead.

The shape comes from what **git tracks**, so a vendored `node_modules` is neither counted as
your language mix nor walked at all.

### Then it adopts

```bash
mneme adopt payments-service \
  --description "Operational knowledge about running the payments-service: card settlement, refunds, chargeback webhook handling, and the RDS ledger cluster. Gateway error codes and retry behaviour. Excludes anything about the wider widget platform." \
  --owner pay-team
```

```
mode: plain — the repo is not a knowledge plugin, so mneme keeps to one directory
added: MNEME.md
added: mneme-index/SKILL.md
added: mneme-index/CONTRIBUTING.md
added: .github/workflows/mneme-validate.yml
added: CODEOWNERS
added: mneme-index/facts/.gitkeep
review and commit these files through your repo's normal process
```

### What it deliberately does not do

Compare that list against Path C's. Everything absent is absent on purpose:

| Not written | Why |
|---|---|
| `.claude-plugin/plugin.json`, `marketplace.json` | The repo is not being published as a plugin. Writing a manifest would make it one. |
| `.github/workflows/release.yml` | It bumps a version inside a manifest that will not exist. |
| Root `CONTRIBUTING.md` | The repo has its own, about its own code. mneme's goes in `mneme-index/CONTRIBUTING.md`, inside the directory it governs. |
| Anything under `skills/` | In a plain repo, `skills/` is the application's — mneme does not lint it, write to it, or fail a harvest over it. |

Two more differences worth seeing:

**CODEOWNERS is scoped.** Adopting a service must not route every pull request in it to the
people who agreed to review facts:

```
# Reviewers for the knowledge mneme maintains in this repo.
# The rest of the repo keeps whatever ownership it already had.
/mneme-index/ @pay-team
```

If the repo already has a CODEOWNERS, adopt **does not touch it** — it prints the line for
you to add by hand, because a file that routes code review is the last place for a tool to
make a silent edit.

**CI is path-scoped and cannot collide.** It lands as `mneme-validate.yml`, not
`validate.yml`, and every trigger is filtered:

```yaml
name: mneme knowledge validate
on:
  pull_request:
    paths:
      - "mneme-index/**"
  push:
    branches: [main]
    paths:
      - "mneme-index/**"
```

Your CI budget is not mneme's to spend on pull requests that do not touch a fact.

### The loop is the same

Everything in [section 6](#6-the-loop-sharing-what-you-learn) works exactly as it does for a
knowledge plugin. Flag, distil, review, approve — and the harvest lands on a branch:

```
harvested payments-service: 1 units on mneme/harvest-20260814-160249
pr: no remote — branch left local; merge it or add a remote and push
```

```bash
git diff --stat main..mneme/harvest-20260814-160249
```

```
 mneme-index/SKILL.md             | 3 ++-
 mneme-index/facts/chargebacks.md | 4 ++++
 2 files changed, 6 insertions(+), 1 deletion(-)
```

```bash
git show mneme/harvest-20260814-160249:mneme-index/facts/chargebacks.md
```

```
---
topic: chargebacks
---
- [gotcha] The chargeback webhook replays events for up to 72 hours, so the handler must key on event_id and not on transaction_id #chargebacks (verified: 2026-08-14)
```

`main` is never written, in either mode. Two files changed, both inside `mneme-index/`, and
nothing else in the service was touched.

### What plain mode does not give you

Three things, stated plainly so you can decide before you adopt:

**No `/mneme:classify`.** The librarian pass files loose facts *into* destination skills, and
a plain repo has none. It refuses rather than guessing:

```
mneme: …/payments-service is not a knowledge plugin, so it has no destination skills to
file facts into — classify has nowhere to put anything. What does work here: `mneme share`
captures facts into mneme-index/, `mneme review` accepts a pull request and the facts inside
it, and `mneme migrate` moves a legacy facts/ directory. To make this repo a plugin instead,
run: mneme adopt <name> --as-plugin
```

**No marketplace distribution.** Without a plugin manifest there is nothing for another
person to `/plugin install`. They get the knowledge by cloning the repo, or by registering it
with their own mneme.

**No automatic in-repo context.** Claude Code discovers skills in `.claude/skills/`; a
top-level `mneme-index/` is inert to it. A teammate with mneme finds the knowledge through
`mneme search` like any other registered scope. A teammate without mneme finds it by reading
the files — which is why the router table in `mneme-index/SKILL.md` lists every topic.

If you want the other three, `mneme adopt <name> --as-plugin` gives you Path C's full
scaffold instead.

---

## 6. The loop: sharing what you learn

This is the part that matters. Knowledge gets in through a pipeline with two gates — a
deterministic machine gate and you — and it never skips either.

```
work session → flag → distiller → machine gate → staging → YOUR review → branch → PR → merge
```

### 6.1 Flag it in the moment

When something is worth keeping, say so. Don't stop to write documentation:

```
/mneme:capture the lb keeps stale targets for ~90s after a deploy drains them; the fix is to wait on the health check, not the deploy event
```

```bash
mneme flag "the lb keeps stale targets for ~90s after a deploy drains them; the fix is to wait on the health check, not the deploy event"
```

```
flagged
```

When installed knowledge turns out to be *wrong* — that is feedback too, and it travels the
same pipeline:

```bash
mneme flag --kind knowledge-issue "the staging-env fact says 04:00 UTC but the reset moved to 05:00"
```

```
flagged
```

You mostly won't type these yourself. A SessionStart hook injects a short brief telling the
agent to flag hard-won fixes as they land, so this happens while you work:

```markdown
## mneme noticing

While you work, flag knowledge worth keeping — do NOT stop to document it.

Flag (one line each, at the moment it happens) when:
- a hard-won fix lands after real dead ends: `mneme flag "<what worked + why it was non-obvious>"`
- installed knowledge proves wrong or stale: `mneme flag --kind knowledge-issue "<what is wrong>"`

Rules: one line per flag; no mid-session distillation (a background distiller runs later);
never flag anything from excluded repos/paths; never include secrets or credentials in flag text.
```

That brief mentions "excluded repos/paths". You set those per plugin when you register it —
`mneme registry add <name> --repo <url> --exclude 'secrets/**' --exclude 'customer-data/**'` —
and it is worth knowing which of mneme's controls are machine-enforced and which are not:

| Control | Enforced by |
|---|---|
| Secret scan → quarantine, lint, dedup, the declined ledger | The machine gate — deterministic, not skippable |
| Nothing reaches a repo without your approval; nothing touches `main` | The pipeline itself |
| Exclusions, one-line-per-flag, "don't flag secrets" | The agent honoring the brief — guidance, not a wall |

Treat exclusions as a strong hint to the agent, not as a guarantee. The guarantee is the
secret scanner behind it, and your own eyes at the share gate.

Check what is pending any time:

```bash
mneme status
```

```
plugins: 3 registered
- acme-knowledge [internal]
- team-kb [internal]
- personal-kb [internal]
flags: 2 pending
staging: 0 staged, 0 quarantined, 0 declined (ledger)
submissions: 0 recorded
index: not built
```

### 6.2 The distiller runs in the background

Flags are raw notes, not knowledge. When your session stops (or compacts), a `Stop` hook
kicks off a background distiller that reads the session transcript plus your flags and
proposes structured units — skills for procedures, facts for durable statements — each
routed to a plugin by matching against scope statements.

Two preconditions decide whether it runs at all:

- **At least one flag must be pending.** The hook checks first and exits immediately if there
  are none — a session with no flags produces nothing, no matter what is in the transcript.
- **It runs on *stop*, not during the session.** Nothing is distilled while you are still
  working.

> **This is the step that surprises people.** If you flag something and immediately run
> `/mneme:share`, you will get `nothing staged` — correctly. The distiller has not run yet.
> **End the session (or `/compact`), give it a minute or two, then start a new session and
> run `/mneme:share`.** It is a detached background process that shells out to a headless
> model run, so it takes a little while and reports nothing while it works.

It is asynchronous and silent by design: nothing interrupts your session, and there is no
progress bar. If candidates do not show up, these two are where to look:

```bash
mneme status                     # flags: N pending → not distilled yet; staging: N staged → ready
tail ~/.mneme/logs/distill.log   # what the detached run actually did
```

Every proposal then passes a **machine gate** before you ever see it: it is rendered into a
canonical unit, checked against the declined-candidate ledger, deduplicated, secret-scanned,
tagged with a similar existing unit if there is one, and flagged if it is routing toward a
less-restricted repo. The gate reports what it did:

```
staged 2  quarantined 1  skipped-declined 0  skipped-duplicate 0  rejected 0  boundary-warnings 0
```

That `quarantined 1` is the secret scanner catching a proposal that contained a token.
Quarantined candidates cannot be applied at all.

### 6.3 Review the queue — the human gate

```
/mneme:share
```

```bash
mneme share list
```

```
acme-knowledge:
  fact-b412621c7948  fact/new  conf=0.9
  skill-2fa4621ba0ec  skill/new  conf=0.8
```

Candidates are grouped by the plugin they are routed to. Three annotations matter:

| Annotation | Meaning |
|---|---|
| `[boundary]` | Routed toward a *less-restricted* repo than its source. Confirm explicitly before approving. See the caveat in [§9](#9-rolling-it-out-to-a-team) — the background pipeline does not currently raise this. |
| `[similar: <unit>]` | The nearest full-text hit in the index — a **hint, not a match**. It is the top result for an OR-query over the candidate's words with no similarity threshold, so unrelated units do show up. Read the named unit before assuming a duplicate. Only appears once the index is enabled. |
| `[QUARANTINED]` | The secret scanner found a blocker. **Cannot be applied.** Needs redaction first. |

Quarantined candidates are hidden unless you ask for them:

```bash
mneme share list --all
```

```
acme-knowledge:
  fact-3b084049cdde  fact/new  conf=0.5 [QUARANTINED]
  fact-b412621c7948  fact/new  conf=0.9
  skill-2fa4621ba0ec  skill/new  conf=0.8
```

Read anything before deciding. A new fact shows the exact bullet that would be written:

```bash
mneme share diff fact-b412621c7948
```

```
- [gotcha] The load balancer keeps stale targets for about 90 seconds after a deploy drains them #deploy #lb (verified: 2026-08-13)
```

A new skill shows the whole unit, including the failure pattern that makes it worth keeping:

```bash
mneme share diff skill-2fa4621ba0ec
```

```markdown
---
name: drain-a-widget-deploy
description: Drain a widget deploy without serving stale targets
metadata:
  mneme-type: skill
  mneme-source: "session:demo"
  mneme-captured: 2026-08-13
  mneme-last-verified: 2026-08-13
---
# drain-a-widget-deploy

## Procedure

1. Start the deploy.
2. Wait on the health check, not the deploy event.
3. Confirm the target group is empty before cutting over.

## Failure pattern

Watching the deploy event reports success about 90 seconds before the load balancer stops
serving stale targets.
```

For an update to an existing unit, `share diff` shows a unified diff instead.

Want to see what would happen without doing it:

```bash
mneme share apply --ids fact-b412621c7948,skill-2fa4621ba0ec --dry-run
```

```
would apply fact-b412621c7948 -> acme-knowledge (fact/new)
would apply skill-2fa4621ba0ec -> acme-knowledge (skill/new)
```

### 6.4 Decline with a reason

Declining is not just deleting. It records the candidate in `~/.mneme/declined.jsonl` under a
date-independent content hash, and the **machine gate** drops any later proposal of the same
knowledge before it ever reaches your queue — you see it as `skipped-declined` in the gate
line:

```bash
mneme decline skill-2fa4621ba0ec --reason "the drain procedure is already covered by the platform runbook"
```

```
declined skill-2fa4621ba0ec
```

The suppression is keyed on the content hash, not on what you write — but write a real reason
anyway. It is the record a future maintainer reads when they wonder why this knowledge is not
in the repo, and it is the only part of the decline a human will ever see.

### 6.5 Approve, and the harvest becomes a PR

```bash
mneme share apply --ids fact-b412621c7948
```

Without a remote:

```
harvested acme-knowledge: 1 units on mneme/harvest-20260813-130954
pr: no remote — branch left local; merge it or add a remote and push
```

With a remote, that second line is the pull request instead:

```
harvested acme-knowledge: 1 units on mneme/harvest-20260813-130954
pr: https://github.com/acme/acme-knowledge/pull/42
```

If `gh` is not installed, the branch is still pushed and mneme tells you to open the PR
yourself. Use `--no-push` to keep a branch local this once.

**Your `main` never moved.** The approved unit landed on a branch:

```
09c8990 knowledge: harvest 2026-08-13 (1 units)
03a2847 chore: scaffold acme-knowledge knowledge plugin
---
* main
  mneme/harvest-20260813-130954
--- current branch:
main
```

The commit carries provenance back to the session that produced it:

```
knowledge: harvest 2026-08-13 (1 units)

- facts/deploys#the-load-balancer-keeps-stale-targets (new fact)

Mneme-Source: session:demo
```

And the fact itself, written into the canonical facts directory with the index updated
alongside it:

```markdown
---
topic: deploys
---
- [gotcha] The load balancer keeps stale targets for about 90 seconds after a deploy drains them #deploy #lb (verified: 2026-08-13)
```

### 6.6 Merge it

**Which of these applies is decided by the `pr:` line you just got.**

If it printed a pull request URL, you are done here: review and merge that PR the way you
would any other. Do not merge the branch locally — that would bypass the CODEOWNERS review
and the CI that the PR exists to run.

If it printed `no remote — branch left local`, there is no PR to review, so you are both
author and reviewer. Read the diff first, then merge:

```bash
cd ~/.mneme/repos/personal-kb
git diff main..mneme/harvest-20260813-130954     # review it — nothing else will
git merge --no-edit mneme/harvest-20260813-130954
```

```
Updating acaf7da..7f950ec
Fast-forward
 skills/knowledge-index/SKILL.md         | 3 ++-
 skills/knowledge-index/facts/deploys.md | 4 ++++
 2 files changed, 6 insertions(+), 1 deletion(-)
 create mode 100644 skills/knowledge-index/facts/deploys.md
```

That is the loop closed: a thing you learned mid-session is now a reviewed, dated,
attributed unit on `main`.

---

## 7. Getting knowledge back out

Contributed knowledge is only worth the contributing if it comes back. Make it searchable
once:

```bash
mneme db enable
```

```
indexed acme-knowledge: 1 skills, 1 facts, 0 skipped
indexed team-kb: 1 skills, 3 facts, 0 skipped
indexed personal-kb: 1 skills, 3 facts, 0 skipped
index enabled at ~/.mneme/mneme.db
```

A non-zero `skipped` count means a file the indexer could not read — `mneme index rebuild`
names each one and why.

Then search across every registered plugin at once. Terms are OR-matched and ranked, so
vague works:

```bash
mneme search "stale targets after deploy"
```

```
-8.41	acme-knowledge	facts/deploys#the-load-balancer-keeps-stale-targets	The load balancer keeps stale targets for about 90 seconds after a deploy drains them
-0.73	acme-knowledge	skills/knowledge-index	Consult when you need durable facts from acme-knowledge — constraints, gotchas, decisions, and runbook notes. Widget platform operations at Acme: deploy paths, incident runbooks, and the constraints of the billing pipeline. Excludes customer data and anything about the marketing site. Topics listed in this skill route to fact files under facts/. Topics: deploys
```

Tab-separated columns are score, plugin, unit id, description, best match first. The score is
a relative BM25 rank over whatever is currently indexed — more negative is a better match, and
the absolute number means nothing on its own, so don't expect to reproduce these exactly.

The second row is the mechanically generated `knowledge-index` skill, which is itself indexed
and matches most queries about the repo. That is working as intended — it is the router — but
`--kind fact` filters it out when you only want the facts themselves.

Filter with `--kind skill` / `--kind fact` and `--plugin <name>`; refresh after merges with
`mneme index rebuild`.

You will rarely run this yourself either. Mneme ships a retrieval skill that has the agent
search installed knowledge by vague notion *before* reinventing something the organization
has already solved — which is the whole point of putting the knowledge there.

---

## 8. Keeping a repo healthy

**`/mneme:status`** — the pipeline at a glance: plugins, pending flags, staging, submissions,
index freshness.

**`/mneme:verify <name>`** — the staleness sweep. Exit code 2 means stale units were found;
that is a report, not an error:

```bash
mneme verify team-kb --days 90
```

```
facts/staging-env#restore-staging-from-the-nightly-snapshot  last-verified=2025-11-02  age-days=284
facts/staging-env#staging-keeps-7-days-of-snapshots  last-verified=none  age-days=unknown
stale 2 of 3 units
```

A unit with no verified date is always stale. The command then helps you re-verify each one,
and corrections flow back through the normal flag → distill → share pipeline rather than
being edited in place.

**`/mneme:classify`** — for **plugin** repos only; a plain repo has no destination skills to
file facts into and the command refuses, naming what does work there. Once a repo has taken
on a few merged PRs' worth of facts, run this *from inside the repo* (the current directory
is the argument). It reads every accumulated
fact, proposes a complete mapping of fact → the skill whose work it belongs to, and waits for
your approval before editing anything. A fact has three possible endings and no others: it
lands in a skill with its sentence carried across verbatim, it stays a fact, or it is
*retired* as a duplicate — which requires naming the unit that already covers it
(`--retire <retired-unit-id>=<covering-unit-id>`). A retirement is the only way knowledge
leaves the repo, and every one is printed in the pull request so you approve the removal
rather than just the reorganization. Delivered as its own PR.

**`/mneme:review`** — for maintainers of a repo others contribute to. Run it from inside the
repo (requires `gh`). It reads every open PR, annotates each fact each one adds as duplicate,
previously-declined, possibly-integrated, or genuinely new, and then does only what you
approve for that specific PR: merge it, close it as a duplicate, or extract just the new
bullets onto mneme's own branch. Nothing is merged or closed on your behalf.

---

## 9. Rolling it out to a team

A knowledge plugin distributes itself — the repo is its own marketplace, so consumers run one
command and inherit every future merge through normal plugin updates:

```
/plugin marketplace add acme/acme-knowledge
/plugin install acme-knowledge@acme-knowledge
```

Three things are worth setting deliberately before you invite contributors:

- **CODEOWNERS** — scaffolded with a fallback owner; add per-area rules as the repo grows, so
  the right people review the right knowledge.
- **CI** — `validate.yml` runs mneme's lint and secret scan on every PR. Machines settle
  format; humans judge substance. That split is what keeps review throughput survivable.
- **Sensitivity** — `public | internal | restricted` per plugin. This label is what routing
  and review reason about, and it is worth setting deliberately.

  **Do not treat it as an enforced control today.** Mneme can flag a candidate routed from a
  more-restricted context toward a less-restricted repo (`[boundary]` at the human gate), but
  that check only runs when the ingest is told which plugin the knowledge came from
  (`mneme distill ingest --source-plugin <name>`), and the background distiller does not pass
  it. In the shipped pipeline the flag never appears. Until that is wired up, the thing that
  actually keeps restricted knowledge out of a public repo is you, reading the target on each
  candidate at the `/mneme:share` gate.

Governance is your existing git governance: branch protection, required reviews, audit
history. Mneme adds no vendor service and no second permission model.

---

## 10. Where to go next

- [docs/install.md](install.md) — installation, what the hooks do, configuration environment
  variables, troubleshooting, uninstalling.
- [The design spec](superpowers/specs/2026-08-11-mneme-design.md) — the architecture and the
  reasoning behind the gates.
- [The prior-art survey](research/2026-08-11-prior-art.md) — the landscape mneme was built
  into, and why this intersection was empty.
- `mneme <command> --help` — every command, every flag.
