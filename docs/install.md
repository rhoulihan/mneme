# Installing mneme in Claude Code

Mneme ships as a Claude Code plugin, and its repository is its own marketplace — one `marketplace add` and every future release arrives as a normal plugin update.

**Requirements:** Claude Code, Python ≥ 3.10, and git. The engine is standard-library-only — there is nothing to `pip install`.

**Optional, except for review:** the GitHub CLI ([`gh`](https://cli.github.com), authenticated with `gh auth login`). Harvest and classify degrade gracefully without it — the branch is pushed and you open the pull request yourself. `/mneme:review` cannot: reading your repo's open pull requests *is* the command, so `mneme review triage` fails with a message naming the requirement when `gh` is missing or unauthenticated.

## 1. Install the plugin

```
/plugin marketplace add rhoulihan/mneme
/plugin install mneme@mneme
```

That registers the marketplace defined by `.claude-plugin/marketplace.json` at the repo root and installs the `mneme` plugin it points at. The plugin contributes:

- **hooks** — five of them (`hooks/hooks.json`): SessionStart, Stop, PreCompact, UserPromptSubmit and SubagentStop. §3 says what each does.
- **skills** — the `/mneme:*` commands (`capture`, `share`, `new`, `register`, `adopt`, `status`, `verify`, `classify`, `review`, `index`) plus a model-invocable `retrieval` skill.
- **an MCP server** — `mneme` (`bin/mneme-mcp`), exposing one tool, `mneme_flag`. Flag text with quotes, `$`, backslashes or newlines goes in without any shell quoting.
- **`bin/`** — `mneme`, `mneme-index`, `mneme-mcp` and `mneme-distill-pipeline`. Claude Code puts `bin/` on the Bash `PATH` while the plugin is enabled.

To install from a local checkout instead (development, air-gapped machines):

```
/plugin marketplace add /path/to/mneme
/plugin install mneme@mneme
```

## 2. First run

Initialize local state (defaults to `~/.mneme`):

```bash
mneme init
```

Then give mneme somewhere to put knowledge. Either create a new governed knowledge plugin:

```
/mneme:new acme-knowledge
```

which interviews you for the scope statement before scaffolding the repo — or register a repo you already have:

```
/mneme:register acme-knowledge git@github.com:acme/knowledge.git
```

The CLI equivalents, if you prefer driving it directly:

```bash
mneme new acme-knowledge --owner your-team --sensitivity internal
mneme registry add acme-knowledge --repo git@github.com:acme/knowledge.git --clone
mneme registry add personal-kb --repo git@github.com:you/kb.git --path ~/src/kb
```

**It does not have to be a knowledge repo.** Register an ordinary app, service or infra
repo and `/mneme:adopt` gives it a `mneme-index/` directory at the root — router plus facts
— without turning it into a plugin. It writes no manifests, does not touch the repo's own
`skills/` or `CONTRIBUTING.md`, scopes CODEOWNERS to `/mneme-index/`, and installs CI that
only runs when the knowledge changes. `mneme status` names each registered repo's mode.
`/mneme:share` and `/mneme:review` work there exactly as they do in a knowledge plugin;
`/mneme:classify` is the one command that does not, because a plain repo has no destination
skills to file facts into.

There is no per-repo contribution setting to choose. Approved knowledge always lands on a
`mneme/harvest-*` branch: with a remote, mneme pushes it and opens a PR; without one, the
branch stays local for you to merge or push. Mneme never commits to a registered repo's
`main` — personal repos included.

Make registered knowledge searchable (optional, recommended):

```bash
mneme db enable         # opt the local SQLite index in
mneme index rebuild     # build/refresh it across all registered plugins
mneme index rebuild --stale   # rebuild only the repos that actually moved
mneme index check       # exits 2 when the index no longer speaks for a repo
```

The index is built from the working trees of registered repos, and nearly every event that
changes one belongs to somebody else — a merged pull request most of all. So `mneme search`
warns on stderr when the index is behind (stdout stays byte-identical, since callers parse
it), `/mneme:index` rebuilds it, and a check that cannot see a repo reports stale rather
than fresh.

Two more surfaces worth knowing before the walkthrough:

- `mneme new <name> --no-plugin` scaffolds a governed knowledge repo that is not published
  as one — no manifests, no release workflow, same layout otherwise.
- `mneme share route <id> --target <plugin>` fixes a mis-routed candidate at the gate
  instead of spending a decline on it (`--allow-boundary` to move toward a less restricted
  repo). The correction sticks: the distiller stops re-proposing the destination you moved
  knowledge off.

Check the whole pipeline at any time with `/mneme:status` (or `mneme status`).

Once a repo has taken on a few merged PRs' worth of facts, run the librarian pass from
inside it: `cd` into the knowledge repo and run `/mneme:classify`. The current directory is
the argument — there is no plugin name to pass, and the command says so plainly if the
directory is not a registered knowledge plugin, or is registered but is a plain repo whose
knowledge lives in `mneme-index/`. It reads every accumulated fact, proposes a
complete mapping of fact → the skill whose work it belongs to, and **waits for your
approval** before editing anything, then regenerates the knowledge-index and delivers the
whole reorganization as its own `mneme/classify-*` branch and PR. A fact either lands in a
skill (sentence carried across verbatim), stays a fact, or is retired as a duplicate — and a
retirement must name the unit that covers it, so nothing leaves silently. Change your mind at any point and
`mneme classify abort` puts the repo back as it was.

**Repos older than 0.5.0.** Facts live at `skills/knowledge-index/facts/`; a repo scaffolded
before that carries a top-level `facts/` instead. It is read exactly as before — lint,
verify, search and the index sweep both layouts — but nothing new is written there: every
new topic goes to the canonical directory, and the next contribution of any kind (a harvest,
a classify pass, a review extraction) moves the old files into it on that contribution's own
branch, with history preserved and the moves listed in the pull request. There is nothing to
opt into and no user action required. For a repo with nothing else pending — no staged
candidates, nothing to classify — run the migration on its own:

```bash
cd path/to/knowledge/repo
mneme migrate            # mneme/migrate-* branch + PR; --no-push leaves it local
```

`mneme status` lists the registered plugins that still need it
(`legacy facts layout: <name> (run: mneme migrate in that repo)`), and says nothing when
none do.

When you maintain a repo other people contribute to, run `/mneme:review` from inside it (the
current directory is the argument here too, and this is the one command that requires `gh`).
It reads every open pull request and annotates each fact bullet they add — already in the
repo, previously declined by a human, already integrated into a skill, possibly covered
by an existing skill, or genuinely new — recommends one verdict per PR, and then does only what you approve for that specific
PR: merge it, close it as a duplicate with a comment naming the covering units, or extract
just the new bullets onto a `mneme/review-*` branch and open mneme's own PR
(`mneme review begin` / `finalize`, with `mneme review abort` to back out). Nothing is
merged or closed on your behalf.

## 3. What the hooks do

Once installed, mneme rides your sessions without being asked.

**SessionStart** (`hooks/scripts/session-start.sh`, matcher `startup|clear|compact|resume`)
Runs `mneme context` and injects its output via
`{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}`.
That output is the *noticing brief* — what counts as hard-won knowledge, which flag path to
prefer, and your registered plugins with their scope statements. It also carries the
**previous session's tally** when there is something to say:

```
Last session: 214 tool calls, 0 flags captured. If nothing durable was learned that is
a fine answer — say so; if something was, flag it now before it is gone.
```

Silent when the last session captured something, or did fewer than 20 tool calls. On
`compact`/`clear`/`resume` it reports the *current* session's own numbers instead, since
that session is continuing. Exits 0 on every path: if mneme is missing or errors, it prints
nothing and the session starts normally.

**UserPromptSubmit** (`hooks/scripts/user-prompt-submit.sh`)
The delivery channel. SessionStart and UserPromptSubmit are the only two events that can put
text in front of the model, and this is the only one that fires repeatedly during a session —
so everything the other hooks notice arrives here, at a turn boundary rather than
mid-analysis:

```
mneme noticed while you worked — flag anything durable, one line each:
- [resolved-error] ORA-06550 then a success of `docker exec`, after 4 failures
Flag with the `mneme_flag` tool (no quoting needed) — or say nothing if none of it is durable.
```

At most three per turn, hardest-won first, never offered twice. Silent when nothing was
noticed.

**SubagentStop** (`hooks/scripts/subagent-stop.sh`, `async: true`)
Mines the report a delegated agent just returned (`mneme session report`). It can see the
report but cannot speak to the parent session, so it records; UserPromptSubmit delivers.
Reports under 400 characters, or carrying no finding, produce nothing.

**Stop and PreCompact** (`hooks/scripts/distill-hook.sh`, `async: true`)
Two jobs, in this order. First it records what the session captured
(`mneme session tally --transcript …` → `~/.mneme/sessions.jsonl`) and detects candidates —
this runs *before* any early exit, because a session that captured nothing is precisely the
one worth recording. Then it triggers distillation, exiting without doing so when:

- the stdin payload is not parseable JSON,
- `stop_hook_active` is true (Claude Code is already continuing because of a Stop hook),
- `MNEME_DISTILLING` is set (the distiller's own child session — nothing is recorded either),
- `transcript_path` is empty, or
- `mneme distill pending` reports zero pending flags.

Otherwise it detaches `bin/mneme-distill-pipeline` with `nohup` and returns. The session
never waits on distillation.

**The pipeline** (`bin/mneme-distill-pipeline`)
`mneme distill prepare --transcript <path>` builds the prompt → a headless `claude -p` run produces proposals as JSON → `mneme distill ingest - --clear-flags --flags-snapshot <bundle>` puts them through the machine gate (schema validation, secret scan, dedup, routing, sensitivity boundaries) and into `~/.mneme/staging/`. Nothing is ever written to a knowledge repo here — that only happens at the human gate, `/mneme:share`, and even then only on a `mneme/harvest-*` branch, never on `main`.

Flags are consumed carefully, because the run takes minutes and the session keeps working: ingest clears only the flags `prepare` snapshotted (anything you capture mid-run stays pending for the next distill), and clears nothing at all when every proposal failed validation — a distiller that misses the schema does not get to eat the flags.

Its stdout and stderr go to `$MNEME_HOME/logs/distill.log`. That file is where you look when candidates do not show up.

### First open in a knowledge repo

Opening a session inside a repo that carries a `MNEME.md` but is not yet
registered makes the session-start brief ask whether you want to register it —
one confirmation wires up `mneme registry add` (using the repo's origin URL
when it has one) and offers `/mneme:adopt` if governance files are missing.
Declining is persisted (`mneme detection decline`, listed by `mneme detection list`), so
that repo is never nudged again — across sessions and compactions.

## 4. Configuration

All configuration is environment variables — there is no config file to manage.

| Variable | Default | What it does |
|---|---|---|
| `MNEME_HOME` | `~/.mneme` | Local state root: `registry.json`, `staging/` (with `flags.jsonl` and `quarantine/`), the `declined.jsonl`, `submitted.jsonl`, `routed.jsonl` and `detection-declined.jsonl` ledgers, `sessions.jsonl` and `noticed.jsonl` (the per-session tally and what the detector noticed), `mneme.db` (the search index), `repos/`, `logs/`, and `.<name>.lock` files. Deliberately **not** `${CLAUDE_PLUGIN_DATA}` — your registry outlives any one plugin install or harness. |
| `MNEME_CLAUDE_BIN` | `claude` | The binary the distiller invokes headlessly. Point it at an absolute path if `claude` is not on the hook's `PATH`. |
| `MNEME_DISTILL_MODEL` | `sonnet` | Model for the headless distiller run. |
| `MNEME_DISTILL_FOREGROUND` | unset | When `1`, the hook runs the pipeline inline instead of detaching it. For debugging and tests only — it makes Stop wait. |
| `MNEME_DISTILLING` | set by the pipeline | Recursion guard. The hook exits 0 immediately when it is set; never set it yourself. |

Per-plugin capture exclusions (repos or paths that must never generate flags) are set on the registry entry:

```bash
mneme registry add acme-knowledge --repo <url> --clone --exclude 'secrets/**' --exclude 'customer-data/**'
```

## 5. Troubleshooting

**No noticing brief at session start.** Run the hook by hand:

```bash
echo '{}' | bash "$CLAUDE_PLUGIN_ROOT/hooks/scripts/session-start.sh"
```

Empty output means `mneme context` produced nothing — usually no registered plugins (`mneme registry list`) or a `MNEME_HOME` the hook cannot see. The script is silent by design; run `mneme context` directly to see the real error.

**Nothing gets distilled.** Check, in order:

```bash
mneme distill pending          # exit 1 / "0" means there was nothing to distill
tail -50 "${MNEME_HOME:-$HOME/.mneme}/logs/distill.log"
```

An empty log with pending flags means the hook exited at a guard; a log full of `claude: command not found` means you need `MNEME_CLAUDE_BIN`.

**Authentication for the headless distiller.** The pipeline invokes `claude -p` as a normal (non-`--bare`) child session, because `--bare` — the documented mode for scripted calls — requires `ANTHROPIC_API_KEY` (or an `apiKeyHelper`) and does *not* use your OAuth login. Running non-bare keeps OAuth-only machines working; recursion is prevented instead by the `MNEME_DISTILLING` env guard plus the `stop_hook_active` check, so the child session's own Stop hook exits immediately without starting another distiller. If you do have `ANTHROPIC_API_KEY` set and prefer the stricter isolation, point `MNEME_CLAUDE_BIN` at a wrapper that adds `--bare`.

**Validating a local checkout.** From the repo root:

```bash
claude plugin validate .            # add --strict to promote warnings to errors
```

This checks `plugin.json`, `marketplace.json`, `hooks/hooks.json`, and every `skills/*/SKILL.md`.

**Knowledge-repo validation** is separate and stays with the repo: `mneme lint path/to/knowledge/repo` (the same check the scaffolded CI workflow runs).

## 6. Uninstalling

Disable or remove mneme from the `/plugin` menu (or `/plugin uninstall mneme@mneme`).

Local state under `MNEME_HOME` and every knowledge repo you created or registered are untouched — they are ordinary git repositories and directories. Delete `~/.mneme` yourself if you want the state gone too.
