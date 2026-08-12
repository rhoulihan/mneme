# Claude Code Plugin Wiring — Verified Reference (2026-08-11)

Research input for Plan 06 (the Claude Code adapter). Verified against https://code.claude.com/docs/en/hooks, /plugins-reference, /skills, /sub-agents, /cli-reference, /headless, and cross-checked with locally installed plugins (superpowers 6.2.0, ralph-loop 1.0.0, feature-dev). No contradictions found between docs and shipping plugins.

## hooks/hooks.json

Location: `hooks/hooks.json` at plugin root (NOT inside `.claude-plugin/`). Shape:

```json
{
  "description": "optional",
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|clear|compact",
        "hooks": [
          { "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}\"/scripts/foo.sh",
            "shell": "bash",
            "timeout": 60,
            "async": false }
        ]
      }
    ]
  }
}
```

- Events relevant to mneme: `SessionStart`, `Stop`, `PreCompact`, `SessionEnd` (many more exist).
- Matchers: omitted/`"*"` = all. `SessionStart` matches the start reason (`startup|resume|clear|compact|fork`); `PreCompact` does not use the matcher (payload carries `trigger` instead).
- Handler fields: `type: "command"`, `command` (shell form; double-quote `${CLAUDE_PLUGIN_ROOT}` exactly as shown), optional `args` (exec form, no shell), `shell: "bash"`, `timeout` (seconds; command default 600s), `async` (background, output not delivered), `asyncRewake` (background + wakes Claude when the hook exits 2).
- Variables (substituted in commands AND exported as env): `${CLAUDE_PLUGIN_ROOT}` (install dir — changes on update), `${CLAUDE_PLUGIN_DATA}` (persistent `~/.claude/plugins/data/<id>/`), `${CLAUDE_PROJECT_DIR}`.

## Hook stdin payloads

Common fields (snake_case): `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name` (+ `prompt_id` v2.1.196+).

- **SessionStart** adds `source: startup|resume|clear|compact|fork`.
- **Stop** adds `stop_hook_active: bool` (true when already continuing due to a Stop hook — check to avoid loops) and `last_assistant_message` (full final response text; more current than the transcript file).
- **PreCompact** adds `trigger: manual|auto` and `custom_instructions` (string|null).

## SessionStart context injection

Stdout from `SessionStart` (also `UserPromptSubmit`) is injected as context on exit 0. Preferred precise form (what superpowers emits):

```json
{ "hookSpecificOutput": { "hookEventName": "SessionStart", "additionalContext": "text" } }
```

Gotcha: Claude Code reads BOTH top-level `additional_context` and `hookSpecificOutput` without dedup — emit only the `hookSpecificOutput` form. SessionStart hooks re-run on `--resume` (matcher `resume`).

## Stop hook control + background work

- Allow stop: exit 0, no `decision` field.
- Block (force continuation): exit 2 (stderr becomes Claude's next input) or `{"decision": "block", "reason": "...", "systemMessage": "..."}`.
- Check `stop_hook_active` before blocking again.
- Long-running work: `async: true` on the handler so Stop is not blocked (600s default timeout otherwise); inside scripts, `nohup ... & disown` with redirected fds is still wise since the hook's process group may be cleaned up.

## Commands and skills (unified in 2026)

- Custom slash commands merged into skills. `commands/*.md` (flat files) still works but is legacy; `skills/<name>/SKILL.md` is current. Every skill is invocable as `/plugin-name:skill-name`; every command file is a skill.
- Frontmatter split: `disable-model-invocation: true` = user-only command; `user-invocable: false` = model-only skill.
- Useful fields: `description`, `when_to_use`, `argument-hint`, `arguments` (named), `allowed-tools` (invoking turn only), `disallowed-tools`, `model`, `effort`, `context: fork` (+ `agent`, `background`), `hooks` (skill-scoped), `paths` (glob auto-activation gate), `shell`.
- Argument substitution is **0-based**: `$ARGUMENTS` = full string; `$0` = first arg (changed from older 1-based `$1`). Inline dynamic context via backtick-bang command blocks.
- Portability: only `name/description/license/compatibility/metadata/allowed-tools` are agentskills.io-spec; claude.ai Skills-API upload hard-fails on extras like `argument-hint`. Claude Code accepts everything.

## agents/ (plugin subagents)

`agents/<name>.md`: frontmatter (`name` required lowercase-hyphen, `description` required, `model: sonnet|opus|haiku|fable|inherit`, `effort`, `maxTurns`, `tools`, `disallowedTools`, `skills`, `memory`, `background`, `isolation: "worktree"`, `color`) + body = the subagent's entire system prompt. NOT honored in plugin agents (ignored for security): `hooks`, `mcpServers`, `permissionMode`. Invoked by auto-delegation on `description` match, `@`-mention, or the Agent tool with the scoped `subagent_type`.

## plugin.json

`.claude-plugin/plugin.json`; the manifest is optional (components auto-discover; name falls back to dir name); if present only `name` is required. Optional: `$schema` (https://json.schemastore.org/claude-code-plugin-manifest.json), `displayName`, `version` (semver; setting it pins updates to explicit bumps), `description`, `author {name,email,url}`, `homepage`, `repository`, `license`, `keywords`, `defaultEnabled`, component path overrides (`skills`, `commands`, `agents`, `hooks`, `mcpServers`, ...), `userConfig`. `bin/` executables are added to Bash PATH while the plugin is enabled. A root `CLAUDE.md` is NOT loaded.

Validation: `claude plugin validate ./plugin-dir` (add `--strict` in CI to promote warnings).

## Headless distiller invocation

- `claude -p "prompt"` (or stdin <=10MB). Flags: `--output-format json` (result text in `.result`; `--json-schema` puts the validated object in `.structured_output`), `--max-turns N`, `--model`, `--effort`, `--allowedTools "Bash(bin/mneme *),Read"`, `--session-id/--resume`, `--no-session-persistence`.
- **`--bare`** is the documented mode for scripted calls: skips hooks/skills/plugins/MCP/CLAUDE.md — prevents mneme's own hooks from firing recursively in the child. Caveat: bare mode requires `ANTHROPIC_API_KEY` (or `apiKeyHelper`) — it does not use OAuth login. Fallback for OAuth-only machines: non-bare child with `--setting-sources` narrowed and mneme's Stop hook guarded (e.g., a lockfile or env marker) against recursion.
- In `-p` runs, background Bash tasks are killed ~5s after the final result; SIGTERM aborts the turn (exit 143).

## Implications for the mneme adapter (Plan 06)

1. SessionStart hook (matcher `startup|clear|compact|resume`) runs `bin/mneme context` and wraps stdout in `hookSpecificOutput.additionalContext`; must exit 0 even when mneme is unconfigured.
2. Stop + PreCompact hooks share one script: read stdin JSON (`transcript_path`, `session_id`, `stop_hook_active`/`trigger`), exit 0 fast when no flags exist, otherwise run the distiller pipeline detached (`async: true` handler + `nohup` inside): `mneme distill prepare --transcript <path> | claude -p <prompt> --output-format json ... | mneme distill ingest - --clear-flags`. Never block, never emit `decision`.
3. Use `${CLAUDE_PLUGIN_ROOT}` for script paths; keep state in `MNEME_HOME` (not `${CLAUDE_PLUGIN_DATA}`, which is plugin-install-scoped — mneme's registry outlives any one harness).
4. Ship user-facing commands as skills with `disable-model-invocation: true` where purely imperative (`/mneme:share`, `/mneme:new`, `/mneme:status`, `/mneme:verify`, `/mneme:capture` with `argument-hint`), and behavioral skills (harvest review UX, retrieval guidance) as model-invocable.
5. `claude plugin validate --strict` belongs in the engine repo's CI once the manifest lands.
