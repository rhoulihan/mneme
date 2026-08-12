# Mneme Plan 06 — Claude Code Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make mneme installable and live inside Claude Code: the engine's own plugin manifest and marketplace, SessionStart context injection, the Stop/PreCompact background distiller pipeline, the `/mneme:*` command skills (share, capture, new, status, verify, adopt), the model-invocable retrieval skill, plus the `mneme status` and `mneme distill pending` CLI helpers the wiring needs.

**Architecture:** Everything follows `docs/research/2026-08-11-claude-code-plugin-wiring.md` (verified against live docs and shipping plugins — treat it as authoritative; where it and this plan disagree, the reference wins and the deviation is recorded). Hook scripts are thin bash wrappers over the tested Python CLI: `session-start.sh` wraps `mneme context` output in the exact `hookSpecificOutput` JSON contract; `distill-hook.sh` (shared by Stop and PreCompact) parses the stdin payload, exits fast when there is nothing to distill or a guard trips, and detaches `bin/mneme-distill-pipeline` — the `prepare | claude -p | ingest` pipeline with a recursion guard and an env-overridable `claude` binary so tests use a shim. Skills are markdown; user-imperative ones set `disable-model-invocation: true`. All shell logic is testable directly (fixture stdin payloads, shim `claude` on PATH, `MNEME_DISTILL_FOREGROUND=1`).

**Tech Stack:** bash + python3 (already required), markdown. No new dependencies. `claude plugin validate` runs in end-of-plan verification (not in pytest — CI boxes may lack the CLI).

**Spec:** §4.1 (engine plugin anatomy, adapters), §7.1 (SessionStart noticing injection, exclusions), §7.2 (Stop + PreCompact distill triggers, fire-and-forget), §7.5 (status surface), §9 (distiller failure handling). Builds on Plans 01–05.

## Global Constraints

- All prior Global Constraints hold; the full suite (Plan 05's count) stays green after every task.
- **Hook scripts must never break a session:** every hook script exits 0 on every path except where this plan explicitly says otherwise; stderr noise is suppressed; a missing/unconfigured mneme home is a silent no-op.
- **The distiller never blocks Stop/PreCompact:** hooks.json marks the handler `async: true` AND the script detaches its work (`nohup … &`) unless `MNEME_DISTILL_FOREGROUND=1` (tests only).
- **Recursion guard:** the pipeline exports `MNEME_DISTILLING=1`; the hook script exits immediately when it is already set, and also when the Stop payload has `stop_hook_active: true`.
- Scripts resolve the plugin root as `"${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"` so they work installed (variable set by Claude Code) and from a bare checkout (tests).
- The `claude` binary is `"${MNEME_CLAUDE_BIN:-claude}"` — tests shim it; never hardcode.
- New executables recorded with `git update-index --add --chmod=+x` (WSL drvfs).
- Skill frontmatter uses only fields from the wiring reference; every SKILL.md must pass `mneme lint` (our own linter: kebab name matching dir, non-empty description ≤1024).
- Run tests with `python3 -m pytest` from the repo root.

## File Structure

```
.claude-plugin/
├── plugin.json              # Task 3 — the mneme engine plugin manifest
└── marketplace.json         # Task 3 — self-marketplace (add rhoulihan/mneme once)
hooks/
├── hooks.json               # Task 4
└── scripts/
    ├── session-start.sh     # Task 5
    └── distill-hook.sh      # Task 6
bin/
└── mneme-distill-pipeline   # Task 6
skills/
├── capture/SKILL.md         # Task 7
├── status/SKILL.md          # Task 7
├── verify/SKILL.md          # Task 7
├── adopt/SKILL.md           # Task 7
├── register/SKILL.md        # Task 7
├── share/SKILL.md           # Task 8
├── new/SKILL.md             # Task 8
└── retrieval/SKILL.md       # Task 9
core/mneme_core/cli.py       # Task 1 (status), Task 2 (distill pending)
docs/install.md              # Task 10
README.md                    # Task 10 (status table + install pointer)
tests/core/
├── test_cli_status.py       # Task 1
└── test_distill_pending.py  # Task 2
tests/adapter/
├── test_manifests.py        # Task 3
├── test_hooks_json.py       # Task 4
├── test_session_start_hook.py  # Task 5
├── test_distill_hook.py     # Task 6
└── test_skills.py           # Tasks 7–9 (extended per task)
```

---

### Task 1: `mneme status` aggregator

**Files:**
- Modify: `core/mneme_core/cli.py`
- Create: `tests/core/test_cli_status.py`

**Interfaces:**
- Consumes: `staging.load_candidates`, `flags.read_flags`, `registry.load_registry`, `paths` (db_path, submitted_path, declined_path).
- Produces: `mneme status` — a compact plain-text dashboard (spec §7.5):
  ```
  plugins: 2 registered
  - acme-knowledge [internal/pr]
  - personal-kb [internal/commit]
  flags: 1 pending
  staging: 2 staged, 1 quarantined, 3 declined (ledger)
  submissions: 2 recorded, last -> acme-knowledge (mneme/harvest-20260811-120000)
  index: enabled (built 2026-08-11T12:00:00+00:00) | index: not built
  ```
  Every line degrades gracefully (zero counts, `none` markers); exit 0 always, even on a fresh home.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_cli_status.py`:

```python
import json

from mneme_core import flags, paths, registry, staging
from mneme_core.cli import main
from mneme_core.registry import Plugin
from mneme_core.staging import Candidate, candidate_id


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_status_fresh_home(tmp_path, capsys):
    code, out, _ = run(capsys, "--home", str(tmp_path / "h"), "status")
    assert code == 0
    assert "plugins: 0 registered" in out
    assert "flags: 0 pending" in out
    assert "staging: 0 staged, 0 quarantined, 0 declined" in out
    assert "submissions: 0 recorded" in out
    assert "index: not built" in out


def test_status_populated(tmp_path, capsys):
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="acme-knowledge", repo="r", path="/x"))
    flags.add_flag(home, "learned a thing")
    body = "- [gotcha] Something #x (verified: 2026-08-11)\n"
    cand = Candidate(
        id=candidate_id("fact", "acme-knowledge", body), type="fact", edit="new",
        target="acme-knowledge", body=body, topic="t",
    )
    staging.write_candidate(home, cand)
    staging.decline(home, cand, "nope")
    paths.ensure_layout(home)
    record = {"target": "acme-knowledge", "branch": "mneme/harvest-x", "units": ["u"]}
    with paths.submitted_path(home).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    code, out, _ = run(capsys, "--home", str(home), "status")
    assert code == 0
    assert "plugins: 1 registered" in out
    assert "- acme-knowledge [internal/pr]" in out
    assert "flags: 1 pending" in out
    assert "0 staged" in out and "1 declined" in out
    assert "submissions: 1 recorded" in out
    assert "mneme/harvest-x" in out


def test_status_index_enabled(tmp_path, capsys):
    home = tmp_path / "home"
    kb = tmp_path / "kb"
    d = kb / "skills" / "a-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: a-skill\ndescription: d\n---\nBody\n", encoding="utf-8"
    )
    registry.add_plugin(home, Plugin(name="kb", repo="r", path=str(kb)))
    run(capsys, "--home", str(home), "index", "rebuild")
    code, out, _ = run(capsys, "--home", str(home), "status")
    assert code == 0
    assert "index: enabled" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_cli_status.py -v`
Expected: FAIL — argparse `invalid choice: 'status'`.

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

Parser: `sub.add_parser("status")`. Dispatch: `if args.command == "status": return _status_cmd(home)`. Handler:

```python
def _status_cmd(home: Path) -> int:
    import json as json_mod

    from . import flags as flags_mod
    from . import registry as registry_mod
    from . import staging as staging_mod

    plugins = registry_mod.load_registry(home)
    print(f"plugins: {len(plugins)} registered")
    for p in plugins:
        print(f"- {p.name} [{p.sensitivity}/{p.mode}]")
    print(f"flags: {len(flags_mod.read_flags(home))} pending")

    cands = staging_mod.load_candidates(home, include_quarantined=True)
    staged = sum(1 for c in cands if c.status == "staged")
    quarantined = sum(1 for c in cands if c.status == "quarantined")
    declined_file = paths.declined_path(home)
    declined = (
        len([l for l in declined_file.read_text(encoding="utf-8").splitlines() if l.strip()])
        if declined_file.exists()
        else 0
    )
    print(f"staging: {staged} staged, {quarantined} quarantined, {declined} declined (ledger)")

    submitted_file = paths.submitted_path(home)
    records = []
    if submitted_file.exists():
        records = [
            json_mod.loads(l)
            for l in submitted_file.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
    if records:
        last = records[-1]
        print(
            f"submissions: {len(records)} recorded,"
            f" last -> {last.get('target', '?')} ({last.get('branch', '?')})"
        )
    else:
        print("submissions: 0 recorded")

    db_file = paths.db_path(home)
    if not db_file.exists():
        print("index: not built")
    else:
        built = ""
        try:
            from mneme_index import db as index_db

            conn = index_db.open_db_readonly(db_file)
            try:
                row = conn.execute(
                    "SELECT MAX(built_at) AS b FROM plugins"
                ).fetchone()
                built = row["b"] or ""
            finally:
                conn.close()
        except MnemeError:
            built = "unreadable"
        print(f"index: enabled (built {built or 'never'})")
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_cli_status.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_cli_status.py
git commit -m "feat: mneme status dashboard"
```

---

### Task 2: `mneme distill pending`

**Files:**
- Modify: `core/mneme_core/cli.py` (`_distill_cmd`)
- Create: `tests/core/test_distill_pending.py`

**Interfaces:**
- Consumes: `flags.read_flags`.
- Produces: `mneme distill pending` — prints the pending flag count; exit 0 when at least one flag exists, exit 1 when none. The hook script's cheap gate.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_distill_pending.py`:

```python
from mneme_core import flags
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_pending_none(tmp_path, capsys):
    code, out, _ = run(capsys, "--home", str(tmp_path / "h"), "distill", "pending")
    assert code == 1
    assert out.strip() == "0"


def test_pending_some(tmp_path, capsys):
    home = tmp_path / "home"
    flags.add_flag(home, "x")
    flags.add_flag(home, "y")
    code, out, _ = run(capsys, "--home", str(home), "distill", "pending")
    assert code == 0
    assert out.strip() == "2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_distill_pending.py -v`
Expected: FAIL — argparse `invalid choice: 'pending'`.

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

In the `distill` subparser group: `distill_sub.add_parser("pending")`. In `_distill_cmd`, before the other branches:

```python
    if args.distill_command == "pending":
        from . import flags as flags_mod

        count = len(flags_mod.read_flags(home))
        print(count)
        return 0 if count else 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_distill_pending.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_distill_pending.py
git commit -m "feat: mneme distill pending gate"
```

---

### Task 3: Engine plugin manifest + self-marketplace

**Files:**
- Create: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `tests/adapter/test_manifests.py`

**Interfaces:**
- Produces: the mneme engine's own manifest — name `mneme`, `version` matching `mneme_core.__version__`, description, author `Rick Houlihan`, homepage/repository `https://github.com/rhoulihan/mneme`, license `Apache-2.0`, keywords — and a self-referential `marketplace.json` (name `mneme`, owner `rhoulihan`, one plugin entry, `source: "./"`), so `/plugin marketplace add rhoulihan/mneme` is the whole install story.

- [ ] **Step 1: Write the failing tests**

Create `tests/adapter/test_manifests.py`:

```python
import json
from pathlib import Path

import mneme_core

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_plugin_manifest():
    data = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert data["name"] == "mneme"
    assert data["version"] == mneme_core.__version__
    assert data["license"] == "Apache-2.0"
    assert "rhoulihan/mneme" in data["repository"]
    assert data["description"]


def test_marketplace_manifest():
    data = json.loads(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert data["name"] == "mneme"
    assert data["owner"]["name"] == "rhoulihan"
    assert data["plugins"][0]["name"] == "mneme"
    assert data["plugins"][0]["source"] == "./"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/adapter/test_manifests.py -v`
Expected: FAIL — `FileNotFoundError` on plugin.json.

- [ ] **Step 3: Create the manifests**

`.claude-plugin/plugin.json`:

```json
{
  "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
  "name": "mneme",
  "version": "0.1.0",
  "description": "Knowledge-mining engine: captures hard-won knowledge as you work, gates it through machine and human review, and shares it as installable knowledge plugins.",
  "author": { "name": "Rick Houlihan" },
  "homepage": "https://github.com/rhoulihan/mneme",
  "repository": "https://github.com/rhoulihan/mneme",
  "license": "Apache-2.0",
  "keywords": ["knowledge", "memory", "skills", "team", "governance"]
}
```

`.claude-plugin/marketplace.json`:

```json
{
  "name": "mneme",
  "owner": { "name": "rhoulihan" },
  "plugins": [
    {
      "name": "mneme",
      "source": "./",
      "description": "Knowledge-mining engine — the plugin is the memory."
    }
  ]
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/adapter/test_manifests.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add .claude-plugin tests/adapter/test_manifests.py
git commit -m "feat: mneme engine plugin manifest and self-marketplace"
```

---

### Task 4: hooks.json

**Files:**
- Create: `hooks/hooks.json`, `tests/adapter/test_hooks_json.py`

**Interfaces:**
- Produces: the wiring per the reference — SessionStart (matcher `startup|clear|compact|resume`) → `session-start.sh` (timeout 30); Stop and PreCompact → `distill-hook.sh` (`async: true`, timeout 60). Commands use the documented quoting: `"\"${CLAUDE_PLUGIN_ROOT}\"/hooks/scripts/<name>.sh"`, `"shell": "bash"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/adapter/test_hooks_json.py`:

```python
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load():
    return json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))


def test_events_present():
    hooks = load()["hooks"]
    assert set(hooks) == {"SessionStart", "Stop", "PreCompact"}


def test_session_start_wiring():
    group = load()["hooks"]["SessionStart"][0]
    assert group["matcher"] == "startup|clear|compact|resume"
    handler = group["hooks"][0]
    assert handler["type"] == "command"
    assert "session-start.sh" in handler["command"]
    assert '"${CLAUDE_PLUGIN_ROOT}"' in handler["command"]
    assert handler.get("async") is not True


def test_distill_hooks_are_async():
    hooks = load()["hooks"]
    for event in ("Stop", "PreCompact"):
        handler = hooks[event][0]["hooks"][0]
        assert "distill-hook.sh" in handler["command"]
        assert handler["async"] is True
        assert handler["shell"] == "bash"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/adapter/test_hooks_json.py -v`
Expected: FAIL — `FileNotFoundError`.

- [ ] **Step 3: Create `hooks/hooks.json`**

```json
{
  "description": "mneme: session noticing brief + background distillation",
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|clear|compact|resume",
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/scripts/session-start.sh",
            "shell": "bash",
            "timeout": 30
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/scripts/distill-hook.sh",
            "shell": "bash",
            "timeout": 60,
            "async": true
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/scripts/distill-hook.sh",
            "shell": "bash",
            "timeout": 60,
            "async": true
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/adapter/test_hooks_json.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add hooks/hooks.json tests/adapter/test_hooks_json.py
git commit -m "feat: hook wiring — session start injection, async distill triggers"
```

---

### Task 5: SessionStart script

**Files:**
- Create: `hooks/scripts/session-start.sh`, `tests/adapter/test_session_start_hook.py`

**Interfaces:**
- Produces: a bash script that runs `<root>/bin/mneme context` and, when it produces output, prints exactly one line of JSON: `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<the output>"}}` (JSON-escaped via python3 — never hand-escaped). Exits 0 on EVERY path: mneme errors, empty output, missing home. Emits nothing but the JSON (or nothing at all).

- [ ] **Step 1: Write the failing tests**

Create `tests/adapter/test_session_start_hook.py`:

```python
import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "scripts" / "session-start.sh"


def run_script(home):
    env = dict(os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
    return subprocess.run(
        ["bash", str(SCRIPT)], input="{}", capture_output=True, text=True, env=env
    )


def test_emits_hook_specific_output(tmp_path):
    home = tmp_path / "home"
    result = run_script(home)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    inner = payload["hookSpecificOutput"]
    assert inner["hookEventName"] == "SessionStart"
    assert "mneme noticing" in inner["additionalContext"]
    assert "Registered knowledge plugins" in inner["additionalContext"]


def test_exit_zero_when_mneme_broken(tmp_path):
    env = dict(
        os.environ,
        MNEME_HOME=str(tmp_path / "h"),
        CLAUDE_PLUGIN_ROOT=str(tmp_path / "not-a-plugin-root"),
    )
    result = subprocess.run(
        ["bash", str(SCRIPT)], input="{}", capture_output=True, text=True, env=env
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/adapter/test_session_start_hook.py -v`
Expected: FAIL — script not found.

- [ ] **Step 3: Create `hooks/scripts/session-start.sh`**

```bash
#!/usr/bin/env bash
# SessionStart hook: inject the mneme noticing brief + registry summary.
# Contract (docs/research/2026-08-11-claude-code-plugin-wiring.md): emit ONLY
# the hookSpecificOutput JSON form; exit 0 on every path — a broken mneme
# must never break a session.
set -u
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
OUT="$("$ROOT/bin/mneme" context 2>/dev/null)" || exit 0
[ -z "$OUT" ] && exit 0
MNEME_CONTEXT_TEXT="$OUT" python3 - <<'PY' 2>/dev/null || exit 0
import json
import os

print(
    json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": os.environ["MNEME_CONTEXT_TEXT"],
            }
        }
    )
)
PY
exit 0
```

Record it executable:

```bash
chmod +x hooks/scripts/session-start.sh
git update-index --add --chmod=+x hooks/scripts/session-start.sh
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/adapter/test_session_start_hook.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add hooks/scripts/session-start.sh tests/adapter/test_session_start_hook.py
git commit -m "feat: session-start hook — noticing brief injection"
```

---

### Task 6: Distill hook + pipeline

**Files:**
- Create: `hooks/scripts/distill-hook.sh`, `bin/mneme-distill-pipeline`, `tests/adapter/test_distill_hook.py`

**Interfaces:**
- Produces:
  - `bin/mneme-distill-pipeline TRANSCRIPT_PATH` — exports `MNEME_DISTILLING=1`; runs `mneme distill prepare --transcript <path>`, extracts `.prompt` (python3), invokes `"${MNEME_CLAUDE_BIN:-claude}" -p <prompt> --output-format json --max-turns 30 --model "${MNEME_DISTILL_MODEL:-sonnet}" --allowedTools "Read,Grep,Glob"`, extracts `.result`, pipes into `mneme distill ingest - --clear-flags --source "session:<transcript basename>"`. `set -euo pipefail`; failures land in the caller's log, never in the session.
  - `hooks/scripts/distill-hook.sh` — reads the stdin payload; exits 0 immediately when: payload JSON is unparseable, `stop_hook_active` is true, `MNEME_DISTILLING` is set, or `mneme distill pending` exits nonzero. Otherwise: foreground (`MNEME_DISTILL_FOREGROUND=1`) runs the pipeline inline; default detaches it with `nohup … >> $MNEME_HOME/logs/distill.log 2>&1 &`. Always exits 0; never emits `decision` JSON.

- [ ] **Step 1: Write the failing tests**

Create `tests/adapter/test_distill_hook.py`:

```python
import json
import os
import stat
import subprocess
from pathlib import Path

from mneme_core import flags, staging

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "scripts" / "distill-hook.sh"

PROPOSALS = {
    "proposals": [
        {
            "type": "fact", "edit": "new", "target": "unassigned", "topic": "hook-e2e",
            "category": "gotcha", "text": "Distilled through the hook pipeline",
            "tags": ["e2e"], "confidence": 0.9, "rationale": "verified in session",
        }
    ]
}


def make_claude_shim(tmp_path):
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "claude"
    result_doc = json.dumps({"result": json.dumps(PROPOSALS)})
    shim.write_text(f"#!/bin/sh\necho '{result_doc}'\n", encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return shim


def run_hook(tmp_path, home, payload, extra_env=None):
    env = dict(
        os.environ,
        MNEME_HOME=str(home),
        CLAUDE_PLUGIN_ROOT=str(REPO_ROOT),
        MNEME_CLAUDE_BIN=str(make_claude_shim(tmp_path)),
        MNEME_DISTILL_FOREGROUND="1",
    )
    env.pop("MNEME_DISTILLING", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )


def test_full_pipeline_stages_candidate(tmp_path):
    home = tmp_path / "home"
    flags.add_flag(home, "solved something hard", session="s1")
    result = run_hook(tmp_path, home, {"transcript_path": "/tmp/t.jsonl", "stop_hook_active": False})
    assert result.returncode == 0
    cands = staging.load_candidates(home)
    assert len(cands) == 1
    assert "Distilled through the hook pipeline" in cands[0].body
    assert flags.read_flags(home) == []  # --clear-flags consumed them


def test_no_flags_no_work(tmp_path):
    home = tmp_path / "home"
    result = run_hook(tmp_path, home, {"transcript_path": "/tmp/t.jsonl"})
    assert result.returncode == 0
    assert staging.load_candidates(home) == []


def test_stop_hook_active_guard(tmp_path):
    home = tmp_path / "home"
    flags.add_flag(home, "x")
    result = run_hook(
        tmp_path, home, {"transcript_path": "/tmp/t.jsonl", "stop_hook_active": True}
    )
    assert result.returncode == 0
    assert staging.load_candidates(home) == []
    assert flags.read_flags(home) != []  # untouched


def test_recursion_guard(tmp_path):
    home = tmp_path / "home"
    flags.add_flag(home, "x")
    result = run_hook(
        tmp_path, home, {"transcript_path": "/t"}, extra_env={"MNEME_DISTILLING": "1"}
    )
    assert result.returncode == 0
    assert staging.load_candidates(home) == []


def test_garbage_payload_is_silent(tmp_path):
    home = tmp_path / "home"
    env = dict(
        os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT),
        MNEME_DISTILL_FOREGROUND="1",
    )
    result = subprocess.run(
        ["bash", str(SCRIPT)], input="not json", capture_output=True, text=True, env=env
    )
    assert result.returncode == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/adapter/test_distill_hook.py -v`
Expected: FAIL — script not found.

- [ ] **Step 3: Create the two scripts**

`bin/mneme-distill-pipeline`:

```bash
#!/usr/bin/env bash
# The background distiller: prepare -> headless agent -> ingest (spec §7.2).
# Runs detached from the session; failures surface only in the caller's log.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRANSCRIPT="${1:-}"
export MNEME_DISTILLING=1

PROMPT="$("$ROOT/bin/mneme" distill prepare --transcript "$TRANSCRIPT" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["prompt"])')"

"${MNEME_CLAUDE_BIN:-claude}" -p "$PROMPT" \
  --output-format json \
  --max-turns 30 \
  --model "${MNEME_DISTILL_MODEL:-sonnet}" \
  --allowedTools "Read,Grep,Glob" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["result"])' \
  | "$ROOT/bin/mneme" distill ingest - --clear-flags --source "session:$(basename "$TRANSCRIPT" 2>/dev/null || echo unknown)"
```

`hooks/scripts/distill-hook.sh`:

```bash
#!/usr/bin/env bash
# Stop/PreCompact hook: fire-and-forget distillation trigger (spec §7.2, §9).
# Exits 0 on every path; never blocks the session; never emits decision JSON.
set -u
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

[ -n "${MNEME_DISTILLING:-}" ] && exit 0

PAYLOAD="$(cat 2>/dev/null || true)"
TRANSCRIPT="$(MNEME_HOOK_PAYLOAD="$PAYLOAD" python3 - <<'PY' 2>/dev/null || true
import json
import os

try:
    data = json.loads(os.environ.get("MNEME_HOOK_PAYLOAD") or "{}")
except Exception:
    raise SystemExit(1)
if data.get("stop_hook_active"):
    raise SystemExit(1)
print(data.get("transcript_path", ""))
PY
)"
[ -z "$TRANSCRIPT" ] && exit 0

"$ROOT/bin/mneme" distill pending >/dev/null 2>&1 || exit 0

if [ "${MNEME_DISTILL_FOREGROUND:-}" = "1" ]; then
  "$ROOT/bin/mneme-distill-pipeline" "$TRANSCRIPT" >/dev/null 2>&1 || true
else
  LOG_DIR="${MNEME_HOME:-$HOME/.mneme}/logs"
  mkdir -p "$LOG_DIR" 2>/dev/null || true
  nohup "$ROOT/bin/mneme-distill-pipeline" "$TRANSCRIPT" \
    >>"$LOG_DIR/distill.log" 2>&1 &
fi
exit 0
```

Note the payload guard: `stop_hook_active: true` makes the embedded python exit 1, so `TRANSCRIPT` is empty and the script exits 0 without touching flags. A payload without `transcript_path` also exits silently (nothing to distill from).

Record both executable:

```bash
chmod +x bin/mneme-distill-pipeline hooks/scripts/distill-hook.sh
git update-index --add --chmod=+x bin/mneme-distill-pipeline hooks/scripts/distill-hook.sh
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/adapter/test_distill_hook.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add bin/mneme-distill-pipeline hooks/scripts/distill-hook.sh tests/adapter/test_distill_hook.py
git commit -m "feat: background distill pipeline and Stop/PreCompact hook"
```

---

### Task 7: Imperative command skills — capture, status, verify, adopt, register

**Files:**
- Create: `skills/capture/SKILL.md`, `skills/status/SKILL.md`, `skills/verify/SKILL.md`, `skills/adopt/SKILL.md`, `skills/register/SKILL.md`, `tests/adapter/test_skills.py`

**Interfaces:**
- Produces: five user-invocable command skills (`/mneme:capture`, `/mneme:status`, `/mneme:verify`, `/mneme:adopt`, `/mneme:register`), each with `disable-model-invocation: true`, an `argument-hint` where arguments exist, and a body that instructs the agent to run the corresponding `bin/mneme` command (resolving the binary as `"${CLAUDE_PLUGIN_ROOT}/bin/mneme"` when installed, `bin/mneme` in a checkout) and present the result. All five must pass `mneme lint` (name matches dir, description present).

- [ ] **Step 1: Write the failing tests**

Create `tests/adapter/test_skills.py`:

```python
from pathlib import Path

from mneme_core import lint, units

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"

IMPERATIVE = ["capture", "status", "verify", "adopt", "register"]


def test_imperative_skills_exist_and_lint_clean():
    for name in IMPERATIVE:
        d = SKILLS_DIR / name
        assert (d / "SKILL.md").exists(), name
        assert lint.lint_skill(d) == [], name


def test_imperative_skills_are_user_only():
    for name in IMPERATIVE:
        meta, _ = units.parse_frontmatter(
            (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        )
        assert str(meta.get("disable-model-invocation", "")).lower() == "true", name


def test_capture_mentions_flag_command():
    body = (SKILLS_DIR / "capture" / "SKILL.md").read_text(encoding="utf-8")
    assert "mneme flag" in body
    assert "$ARGUMENTS" in body


def test_register_covers_clone_and_adopt():
    body = (SKILLS_DIR / "register" / "SKILL.md").read_text(encoding="utf-8")
    assert "registry add" in body
    assert "--clone" in body
    assert "adopt" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/adapter/test_skills.py -v`
Expected: FAIL — SKILL.md files missing.

- [ ] **Step 3: Create the four skills**

`skills/capture/SKILL.md`:

```markdown
---
name: capture
description: Explicitly flag knowledge worth keeping — a hard-won fix, a non-obvious constraint, a correction to installed knowledge. The background distiller turns flags into staged candidates later.
disable-model-invocation: true
argument-hint: [what you learned and why it was non-obvious]
---

Flag this moment for the mneme distiller.

1. Resolve the mneme binary: `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` if `CLAUDE_PLUGIN_ROOT` is set, else `bin/mneme` from the repo checkout.
2. Run: `mneme flag "$ARGUMENTS"` — if the note describes installed knowledge being wrong or stale, add `--kind knowledge-issue`.
3. Confirm to the user that it is flagged and will be distilled at session end (or compaction). Do not distill now; do not summarize the session.
```

`skills/status/SKILL.md`:

```markdown
---
name: status
description: Show the mneme pipeline state — registered knowledge plugins, pending flags, staged and quarantined candidates, submissions, index freshness.
disable-model-invocation: true
---

Run `mneme status` (binary at `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`) and present the output faithfully. If candidates are staged, remind the user that `/mneme:share` reviews them. If flags are pending, note they distill at session end. Add nothing speculative.
```

`skills/verify/SKILL.md`:

```markdown
---
name: verify
description: Run the staleness sweep over a registered knowledge plugin and help re-verify what it finds.
disable-model-invocation: true
argument-hint: [plugin-name] [--days N]
---

1. Run `mneme verify $ARGUMENTS` (binary at `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`). Exit 2 means stale units were found — that is a report, not an error.
2. Present the stale units grouped by kind. For each, offer to help the user re-verify: check whether the procedure still works or the fact still holds.
3. When the user confirms a unit is still accurate or needs updating, flag it (`mneme flag ...` / `--kind knowledge-issue`) so the correction flows through the normal distill → share pipeline. Never edit knowledge repos directly.
```

`skills/adopt/SKILL.md`:

```markdown
---
name: adopt
description: Retrofit mneme governance onto an existing registered knowledge repo — scope statement, contribution rubric, CODEOWNERS, CI — adding only what is missing.
disable-model-invocation: true
argument-hint: [plugin-name]
---

1. If the repo is not yet registered, register it first: `mneme registry add <name> --repo <url> --clone` (or `--path` for an existing checkout).
2. Ask the user what this plugin's scope should be (what belongs, what does not), then run `mneme adopt $ARGUMENTS --description "<their scope>" --owner "<their team>"` (binary at `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`).
3. Report exactly which files were added, surface any lint warning about legacy content, and remind the user the changes are uncommitted — they review and commit through their repo's normal process.
```

`skills/register/SKILL.md`:

```markdown
---
name: register
description: Register an existing knowledge repo with mneme — from a git URL you have access to (mneme clones it for you) or a local checkout — so its knowledge becomes searchable and it can receive harvested candidates.
disable-model-invocation: true
argument-hint: [plugin-name] [git-url-or-path]
---

Register an existing repo as a knowledge plugin. The binary is `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`.

1. Determine the source from the arguments or by asking: a git URL (GitHub, GitHub Enterprise, GitLab — anything the user can clone) or an existing local checkout.
2. Ask for sensitivity (`public`/`internal`/`restricted`) and contribution mode (`pr` for shared repos, `commit` for personal) if not obvious; defaults are `internal`/`pr`.
3. For a URL: run `mneme registry add <name> --repo <url> --clone [--sensitivity S] [--mode M]`. For a local checkout: run `mneme registry add <name> --repo <url-or-origin> --path <checkout> [...]`.
4. Check the repo's routing readiness: if its `MNEME.md` scope statement is missing (`mneme context` shows "(no scope statement)"), say so and offer `/mneme:adopt <name>` to retrofit governance — without a scope statement the distiller cannot route knowledge to this plugin.
5. Offer to make it searchable now: `mneme index rebuild` (or `mneme db enable` if the index was never enabled).
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/adapter/test_skills.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add skills/capture skills/status skills/verify skills/adopt skills/register tests/adapter/test_skills.py
git commit -m "feat: imperative command skills — capture, status, verify, adopt, register"
```

---

### Task 8: The share and new skills

**Files:**
- Create: `skills/share/SKILL.md`, `skills/new/SKILL.md`
- Modify: `tests/adapter/test_skills.py` (append)

**Interfaces:**
- Produces: `/mneme:share` — the human harvest gate as a conversation: list → diff → per-candidate decision → apply/decline via the CLI, never bypassing it; `/mneme:new` — the conversational scaffold: interview for the scope statement BEFORE creating, then `mneme new` + refine `MNEME.md`. Both `disable-model-invocation: true`, lint-clean.

- [ ] **Step 1: Write the failing tests**

Append to `tests/adapter/test_skills.py`:

```python
def test_share_and_new_skills():
    for name in ("share", "new"):
        d = SKILLS_DIR / name
        assert lint.lint_skill(d) == [], name
        meta, body = units.parse_frontmatter((d / "SKILL.md").read_text(encoding="utf-8"))
        assert str(meta.get("disable-model-invocation", "")).lower() == "true", name


def test_share_flow_covers_the_gate():
    body = (SKILLS_DIR / "share" / "SKILL.md").read_text(encoding="utf-8")
    for token in ("share list", "share diff", "share apply", "decline", "boundary", "QUARANTINED"):
        assert token in body, token


def test_new_interviews_before_creating():
    body = (SKILLS_DIR / "new" / "SKILL.md").read_text(encoding="utf-8")
    assert "MNEME.md" in body
    assert "mneme new" in body
    assert "scope" in body.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/adapter/test_skills.py -v`
Expected: FAIL — new skill dirs missing.

- [ ] **Step 3: Create the two skills**

`skills/share/SKILL.md`:

```markdown
---
name: share
description: Review staged knowledge candidates and harvest the approved ones into their knowledge plugins as commits or pull requests. This is the human gate — nothing is shared without explicit approval here.
disable-model-invocation: true
---

You are driving mneme's harvest gate. The CLI (binary at `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`) does all mechanical work — never edit knowledge repos, staging files, or git state directly.

1. Run `mneme share list` and present the queue grouped by target plugin: id, type/edit, confidence, and every annotation. Call out `[boundary]` flags (candidate routed toward a less-restricted repo — the user must explicitly confirm those) and `[similar: <unit>]` flags (possible duplicate of existing knowledge — suggest comparing before approving). `[QUARANTINED]` candidates (visible with `--all`) contain secret-scan hits and CANNOT be applied; they need redaction first.
2. For each candidate the user wants to inspect, run `mneme share diff <id>` and show the content (new units whole, updates as diffs).
3. Collect decisions conversationally. Rejections: run `mneme decline <id> --reason "<their reason>"` — the reason matters; the distiller uses the ledger to never re-propose it.
4. Approvals: run `mneme share apply --ids <id1>,<id2>,...` (add `--no-push` if the user wants local-only). Report each result line and any PR URL or manual-push instruction verbatim.
5. If a candidate is routed to the wrong plugin, do not apply it — tell the user re-routing lands in a future release and offer decline-and-reflag instead.

Never apply candidates the user has not explicitly approved in this conversation.
```

`skills/new/SKILL.md`:

```markdown
---
name: new
description: Create a new governed knowledge plugin — interview for its scope, scaffold the repo, and refine the scope statement that routes future knowledge to it.
disable-model-invocation: true
argument-hint: [plugin-name]
---

Create a knowledge plugin the router can actually use. The scope statement is the routing prompt — invest in it.

1. Interview the user briefly (2–4 questions): What products/systems/processes does this plugin cover? What explicitly does NOT belong? Who maintains it (owner/team)? How sensitive is it (`public`, `internal`, `restricted`) and should contributions flow by pull request (`pr`, teams) or direct commit (`commit`, personal)?
2. Compose a 2–5 sentence scope statement from their answers — specific names, not generalities.
3. Run `mneme new <name> --description "<scope statement>" --owner "<owner>" --sensitivity <s> --mode <m>` (binary at `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`).
4. Open the generated `MNEME.md`, refine the "What belongs here / What does NOT belong here" sections with the interview specifics, and show the user the final scope statement.
5. Report the repo path and remind them: add a git remote and the plugin distributes itself — consumers run one `marketplace add` and inherit every merged update.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/adapter/test_skills.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add skills/share skills/new tests/adapter/test_skills.py
git commit -m "feat: share and new command skills"
```

---

### Task 9: Retrieval skill (model-invocable)

**Files:**
- Create: `skills/retrieval/SKILL.md`
- Modify: `tests/adapter/test_skills.py` (append)

**Interfaces:**
- Produces: `skills/retrieval/SKILL.md` — model-invocable (NO `disable-model-invocation`), trigger-rich description: when the agent has a vague need that installed knowledge might cover, search the index before reinventing. Lint-clean.

- [ ] **Step 1: Write the failing tests**

Append to `tests/adapter/test_skills.py`:

```python
def test_retrieval_skill_is_model_invocable():
    d = SKILLS_DIR / "retrieval"
    assert lint.lint_skill(d) == []
    meta, body = units.parse_frontmatter((d / "SKILL.md").read_text(encoding="utf-8"))
    assert "disable-model-invocation" not in meta
    assert "mneme search" in body
    assert "mneme db query" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/adapter/test_skills.py::test_retrieval_skill_is_model_invocable -v`
Expected: FAIL — dir missing.

- [ ] **Step 3: Create `skills/retrieval/SKILL.md`**

```markdown
---
name: retrieval
description: Use when you need institutional knowledge that installed knowledge plugins might already hold — a procedure you half-remember exists, a constraint or gotcha about a system named in the task, or before designing something a team may have solved. Searches the mneme index by vague notion.
---

Before reinventing, check what the organization already knows.

1. Resolve the binary: `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` if `CLAUDE_PLUGIN_ROOT` is set, else `bin/mneme`.
2. Run `mneme search "<a few words describing the need>"` — terms are OR-matched and ranked, so cast wide. Filter with `--kind skill` or `--kind fact` and `--plugin <name>` when the target is known.
3. Top hits are entry points: skills route onward through their own SKILL.md; facts carry their category, tags, and verified date. For structured lookups use `mneme db query "SELECT ... FROM units WHERE ..."` (read-only).
4. If the index is not built (`index not built` on stderr), fall back to reading the registered plugins' files directly — `mneme registry list` shows their paths — and suggest `mneme db enable` to the user once.
5. If retrieved knowledge turns out wrong or stale, flag it: `mneme flag --kind knowledge-issue "<what is wrong>"`.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/adapter/test_skills.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add skills/retrieval tests/adapter/test_skills.py
git commit -m "feat: model-invocable retrieval skill"
```

---

### Task 10: Install docs + README status update

**Files:**
- Create: `docs/install.md`
- Modify: `README.md` (status table + install pointer only — nothing else)

**Interfaces:**
- Produces: `docs/install.md` covering: install via `/plugin marketplace add rhoulihan/mneme` + `/plugin install mneme@mneme`; first-run (`mneme init`, `/mneme:new` or `mneme registry add --clone`); how the hooks behave (SessionStart brief, background distillation at Stop/PreCompact, where the log lives: `$MNEME_HOME/logs/distill.log`); configuration env vars (`MNEME_HOME`, `MNEME_CLAUDE_BIN`, `MNEME_DISTILL_MODEL`, `MNEME_DISTILL_FOREGROUND`); the OAuth note for the headless distiller from the wiring reference. README (already plugin-first with an Installing section and a `/mneme:*` command table): flip the Phase 06 row to `✅ merged`, delete the interim caveat sentence ("The plugin surface ships with Phase 06, in progress now — until it merges, the contributor CLI below is the interim interface."), and add one line under Installing pointing at docs/install.md for details. No other README edits.

- [ ] **Step 1: Write the failing test**

Append to `tests/adapter/test_manifests.py`:

```python
def test_install_doc_exists_and_covers_basics():
    text = (REPO_ROOT / "docs" / "install.md").read_text(encoding="utf-8")
    for token in (
        "marketplace add rhoulihan/mneme",
        "MNEME_HOME",
        "MNEME_DISTILL_MODEL",
        "distill.log",
    ):
        assert token in text, token
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/adapter/test_manifests.py -v`
Expected: FAIL — docs/install.md missing.

- [ ] **Step 3: Write `docs/install.md` and update README**

`docs/install.md` — write it fully (install, first-run, hook behavior, env vars, troubleshooting: distiller log location, `claude plugin validate`, the OAuth/`--bare` note from `docs/research/2026-08-11-claude-code-plugin-wiring.md`). README: change the Phase 06 row state to `✅ merged` and add under Quickstart: `Using Claude Code? Install mneme as a plugin instead — see [docs/install.md](docs/install.md).`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/adapter -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add docs/install.md README.md tests/adapter/test_manifests.py
git commit -m "docs: install guide and README status update"
```

---

## Verification (end of plan)

1. `python3 -m pytest -v` — entire suite green.
2. `claude plugin validate . --strict` (or `claude plugin validate .`) from the repo root — manifest, hooks.json, and all seven skills validate. Record warnings verbatim if `--strict` flags any.
3. Hook contract smoke (no real claude needed):
   ```bash
   export MNEME_HOME=$(mktemp -d) CLAUDE_PLUGIN_ROOT="$PWD"
   bin/mneme init && bin/mneme new smoke-kb --owner demo
   echo '{}' | bash hooks/scripts/session-start.sh | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'smoke-kb' in d['hookSpecificOutput']['additionalContext']; print('session-start ok')"
   bin/mneme flag "hook smoke"
   printf '{"transcript_path": "/tmp/t.jsonl", "stop_hook_active": true}' | bash hooks/scripts/distill-hook.sh && bin/mneme distill pending   # guard held: still 1 pending, exit 0
   ```
4. `git log --oneline` shows one commit per task (10 new commits).

## Out of scope for Plan 06 (later plans)

- Plan 07: e2e harness with a real headless distiller run, the dogfood knowledge repo (mneme's own development knowledge), engine-repo CI (pytest + `claude plugin validate --strict`), version bump to 0.2.0.
- Codex adapter (v1.1, with Patrick Meredith) — the hook scripts deliberately isolate everything harness-specific.
- Candidate re-routing at the harvest gate (`share` skill points users to decline-and-reflag for now).
