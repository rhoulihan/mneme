# Installing mneme in Claude Code

Mneme ships as a Claude Code plugin, and its repository is its own marketplace — one `marketplace add` and every future release arrives as a normal plugin update.

**Requirements:** Claude Code, Python ≥ 3.10, and git. The engine is standard-library-only — there is nothing to `pip install`.

## 1. Install the plugin

```
/plugin marketplace add rhoulihan/mneme
/plugin install mneme@mneme
```

That registers the marketplace defined by `.claude-plugin/marketplace.json` at the repo root and installs the `mneme` plugin it points at. The plugin contributes:

- **hooks** — a SessionStart context injector and a Stop/PreCompact background distiller trigger (`hooks/hooks.json`).
- **skills** — the `/mneme:*` commands (`capture`, `share`, `new`, `register`, `adopt`, `status`, `verify`) plus a model-invocable `retrieval` skill.
- **`bin/`** — `mneme` and `mneme-index`, which Claude Code puts on the Bash `PATH` while the plugin is enabled.

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
mneme new acme-knowledge --owner your-team --sensitivity internal --mode pr
mneme registry add acme-knowledge --repo git@github.com:acme/knowledge.git --clone
mneme registry add personal-kb --repo git@github.com:you/kb.git --path ~/src/kb --mode commit
```

Make registered knowledge searchable (optional, recommended):

```bash
mneme db enable        # opt the local SQLite index in
mneme index rebuild    # build/refresh it across all registered plugins
```

Check the whole pipeline at any time with `/mneme:status` (or `mneme status`).

## 3. What the hooks do

Once installed, mneme rides your sessions without being asked.

**SessionStart** (`hooks/scripts/session-start.sh`, matcher `startup|clear|compact|resume`)
Runs `mneme context` and injects its output as session context via
`{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}`.
That output is the *noticing brief* — it tells the agent what counts as hard-won knowledge and lists your registered plugins with their scope statements. The script exits 0 on every path: if mneme is missing, unconfigured, or errors, it prints nothing and the session starts normally.

**Stop and PreCompact** (`hooks/scripts/distill-hook.sh`, `async: true`)
The distill trigger. It exits immediately — doing nothing, touching no state — when:

- the stdin payload is not parseable JSON,
- `stop_hook_active` is true (Claude Code is already continuing because of a Stop hook),
- `MNEME_DISTILLING` is already set (the distiller's own child session), or
- `mneme distill pending` reports zero pending flags.

Otherwise it detaches `bin/mneme-distill-pipeline` with `nohup` and returns immediately. The session never waits on distillation.

**The pipeline** (`bin/mneme-distill-pipeline`)
`mneme distill prepare --transcript <path>` builds the prompt → a headless `claude -p` run produces proposals as JSON → `mneme distill ingest - --clear-flags` puts them through the machine gate (schema validation, secret scan, dedup, routing, sensitivity boundaries) and into `~/.mneme/staging/`. Nothing is ever written to a knowledge repo here — that only happens at the human gate, `/mneme:share`.

Its stdout and stderr go to `$MNEME_HOME/logs/distill.log`. That file is where you look when candidates do not show up.

## 4. Configuration

All configuration is environment variables — there is no config file to manage.

| Variable | Default | What it does |
|---|---|---|
| `MNEME_HOME` | `~/.mneme` | Local state root: registry, flags, staging, quarantine, declined/submitted ledgers, cloned repos, `logs/`. Deliberately **not** `${CLAUDE_PLUGIN_DATA}` — your registry outlives any one plugin install or harness. |
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
