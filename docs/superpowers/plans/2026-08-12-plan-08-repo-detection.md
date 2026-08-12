# Mneme Plan 08 — Session-Start Knowledge-Repo Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a session opens inside a mneme knowledge repo (its `MNEME.md` marker present) that is NOT registered with the local mneme, the SessionStart injection instructs the agent to ask the user whether to register it — offering the exact `mneme registry add` invocation, an `/mneme:adopt` follow-up when governance files are missing, and respecting a decline. Release as 0.3.0.

**Architecture:** Detection is deterministic core code: `routing.find_knowledge_repo(cwd)` walks up from the session's cwd looking for a directory with `MNEME.md`; `mneme context` gains `--cwd DIR` and, when the found repo is not already registered (`routing.plugin_for_path`), appends a clearly-scoped instruction block to the noticing brief. The hook script parses `cwd` from the SessionStart stdin payload (it currently ignores stdin) and passes it through. The AGENT asks the user — hooks cannot prompt interactively; injecting the ask-instruction is the platform-native mechanism.

**Tech Stack:** No new dependencies.

**Spec:** §7.1 (session-start context), §4.2 (registry), plus the user directive: "when a user first opens a session in a plugin repo, if mneme files are detected, the user should be asked if they want to register the repo with the local mneme plugin."

## Global Constraints

- All prior Global Constraints hold; the full suite (392 tests) stays green after every task.
- Detection must be cheap and silent on failure: any error in detection degrades to the plain noticing brief; the SessionStart hook keeps its exit-0-on-every-path contract.
- The nudge appears ONLY when: a `MNEME.md`-bearing directory is found at or above cwd (bounded walk), AND that directory is not inside any registered plugin's path. Never nudge for registered repos, never for repos without the marker, and never instruct the agent to register without asking the user first.
- READ current files before modifying — `routing.py`, `cli.py`, and `hooks/scripts/session-start.sh` were all shaped by earlier plans and audit fixes.
- Run tests with `python3 -m pytest` from the repo root.

## File Structure

```
core/mneme_core/routing.py            # Task 1: find_knowledge_repo
core/mneme_core/cli.py                # Task 2: context --cwd + nudge block
hooks/scripts/session-start.sh        # Task 3: pass payload cwd through
docs/install.md                       # Task 3: one paragraph on first-open detection
tests/core/test_repo_detection.py     # Task 1
tests/core/test_context_nudge.py      # Task 2
tests/adapter/test_session_start_hook.py  # Task 3 (append)
CHANGELOG.md, core/mneme_core/__init__.py, core/mneme_index/__init__.py,
.claude-plugin/plugin.json, pyproject.toml, tests/e2e/test_release.py  # Task 4: 0.3.0
```

---

### Task 1: `routing.find_knowledge_repo`

**Files:**
- Modify: `core/mneme_core/routing.py` (append)
- Create: `tests/core/test_repo_detection.py`

**Interfaces:**
- Consumes: Task 2/3 of Plan 04's `routing` module (`scopes`, `plugin_for_path`).
- Produces: `find_knowledge_repo(cwd: Path, max_depth: int = 20) -> Path | None` — resolves `cwd` and walks upward (at most `max_depth` levels, stopping at the filesystem root) returning the FIRST directory containing an `MNEME.md` file; `None` when nothing is found or on any `OSError` (silent degradation).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_repo_detection.py`:

```python
from mneme_core import routing


def test_finds_marker_in_cwd(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "MNEME.md").write_text("# scope\n", encoding="utf-8")
    assert routing.find_knowledge_repo(kb) == kb.resolve()


def test_finds_marker_in_ancestor(tmp_path):
    kb = tmp_path / "kb"
    deep = kb / "facts" / "sub"
    deep.mkdir(parents=True)
    (kb / "MNEME.md").write_text("# scope\n", encoding="utf-8")
    assert routing.find_knowledge_repo(deep) == kb.resolve()


def test_nearest_marker_wins(tmp_path):
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    (outer / "MNEME.md").write_text("# outer\n", encoding="utf-8")
    (inner / "MNEME.md").write_text("# inner\n", encoding="utf-8")
    assert routing.find_knowledge_repo(inner) == inner.resolve()


def test_none_without_marker(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert routing.find_knowledge_repo(d) is None


def test_max_depth_bounds_the_walk(tmp_path):
    kb = tmp_path / "kb"
    deep = kb
    for i in range(5):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    (kb / "MNEME.md").write_text("# scope\n", encoding="utf-8")
    assert routing.find_knowledge_repo(deep, max_depth=3) is None
    assert routing.find_knowledge_repo(deep, max_depth=10) == kb.resolve()


def test_missing_cwd_is_silent(tmp_path):
    assert routing.find_knowledge_repo(tmp_path / "does-not-exist") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_repo_detection.py -v`
Expected: FAIL — `AttributeError: module 'mneme_core.routing' has no attribute 'find_knowledge_repo'`.

- [ ] **Step 3: Append to `core/mneme_core/routing.py`**

```python
def find_knowledge_repo(cwd: Path, max_depth: int = 20) -> Path | None:
    try:
        current = cwd.resolve()
        if not current.exists():
            return None
        for _ in range(max_depth):
            if (current / "MNEME.md").is_file():
                return current
            if current.parent == current:
                return None
            current = current.parent
    except OSError:
        return None
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_repo_detection.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/routing.py tests/core/test_repo_detection.py
git commit -m "feat: knowledge-repo detection by MNEME.md marker"
```

---

### Task 2: `mneme context --cwd` registration nudge

**Files:**
- Modify: `core/mneme_core/cli.py` (the `context` command)
- Create: `tests/core/test_context_nudge.py`

**Interfaces:**
- Consumes: `routing.find_knowledge_repo`, `routing.plugin_for_path`, `gitops.is_git_repo`, `gitops.git` (for the origin URL, tolerantly).
- Produces: `mneme context [--cwd DIR]`. When `--cwd` is given and `find_knowledge_repo` locates a repo that `plugin_for_path` does NOT place inside any registered plugin, the output gains a final block:

```
## Unregistered knowledge repo detected

<abs path> carries a MNEME.md but is not registered with mneme.
At the START of this session, ask the user whether to register it. If yes, run:
  mneme registry add <suggested-name> --repo <origin-or-local> --path <abs path>
then offer /mneme:adopt <suggested-name> if governance files are missing.
If the user declines, respect that for the rest of the session and do not ask again.
```

  Suggested name: the repo's `.claude-plugin/plugin.json` `name` when present and kebab-valid, else the directory name when kebab-valid, else `<dir-name slugged to kebab>`. `--repo` value: the git `origin` URL when one exists, else `local:<abs path>`. No `--cwd`, registered repo, or no marker → output unchanged. Exit 0 always.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_context_nudge.py`:

```python
import json
import subprocess

from mneme_core import registry
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_kb(tmp_path, name="detected-kb", manifest=True):
    kb = tmp_path / name
    kb.mkdir(parents=True)
    (kb / "MNEME.md").write_text("# scope\n\n## Scope statement\n\nStuff.\n", encoding="utf-8")
    if manifest:
        cp = kb / ".claude-plugin"
        cp.mkdir()
        (cp / "plugin.json").write_text(
            json.dumps({"name": "acme-detected", "version": "0.1.0"}), encoding="utf-8"
        )
    return kb


def test_nudge_for_unregistered_repo(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert code == 0
    assert "Unregistered knowledge repo detected" in out
    assert str(kb.resolve()) in out
    assert "mneme registry add acme-detected" in out
    assert f"local:{kb.resolve()}" in out
    assert "ask the user" in out.lower()
    assert "/mneme:adopt acme-detected" in out


def test_origin_url_used_when_present(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    subprocess.run(["git", "init", "-b", "main", str(kb)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(kb), "remote", "add", "origin", "git@github.com:acme/detected.git"],
        check=True, capture_output=True,
    )
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert "--repo git@github.com:acme/detected.git" in out


def test_no_nudge_when_registered(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    registry.add_plugin(home, Plugin(name="acme-detected", repo="r", path=str(kb)))
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert code == 0
    assert "Unregistered knowledge repo detected" not in out


def test_no_nudge_without_marker_or_cwd(tmp_path, capsys):
    home = tmp_path / "home"
    plain = tmp_path / "plain"
    plain.mkdir()
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(plain))
    assert "Unregistered" not in out
    code, out, _ = run(capsys, "--home", str(home), "context")
    assert code == 0
    assert "Unregistered" not in out


def test_dir_name_fallback_and_slug(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path, name="My Team KB", manifest=False)
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert "mneme registry add my-team-kb" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_context_nudge.py -v`
Expected: FAIL — `--cwd` unrecognized argument.

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

Add `--cwd` to the context subparser: `p_context.add_argument("--cwd", type=Path, default=None)` (introduce `p_context = sub.add_parser("context")` if it is currently unnamed). In the context handler, after the existing output, append:

```python
            if args.cwd is not None:
                nudge = _registration_nudge(home, args.cwd)
                if nudge:
                    print(nudge)
            return 0
```

New module-bottom helper:

```python
def _registration_nudge(home: Path, cwd: Path) -> str:
    import json as json_mod
    import re as re_mod

    from . import gitops, routing
    from .units import KEBAB_RE

    try:
        kb = routing.find_knowledge_repo(cwd)
        if kb is None or routing.plugin_for_path(home, kb) is not None:
            return ""
        name = ""
        manifest = kb / ".claude-plugin" / "plugin.json"
        if manifest.is_file():
            try:
                name = str(json_mod.loads(manifest.read_text(encoding="utf-8")).get("name", ""))
            except (json_mod.JSONDecodeError, OSError):
                name = ""
        if not name or not KEBAB_RE.match(name):
            slug = re_mod.sub(r"[^a-z0-9]+", "-", kb.name.lower()).strip("-")
            name = slug if KEBAB_RE.match(slug) else "detected-knowledge"
        repo_url = f"local:{kb}"
        if gitops.is_git_repo(kb):
            try:
                url = gitops.git(kb, "remote", "get-url", "origin")
                if url:
                    repo_url = url
            except MnemeError:
                pass
        return (
            "\n## Unregistered knowledge repo detected\n\n"
            f"{kb} carries a MNEME.md but is not registered with mneme.\n"
            "At the START of this session, ask the user whether to register it. If yes, run:\n"
            f"  mneme registry add {name} --repo {repo_url} --path {kb}\n"
            f"then offer /mneme:adopt {name} if governance files are missing.\n"
            "If the user declines, respect that for the rest of the session and do not ask again."
        )
    except Exception:
        return ""
```

(The broad `except Exception` is deliberate and commented: detection may never break session start.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_context_nudge.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_context_nudge.py
git commit -m "feat: context --cwd registration nudge for detected knowledge repos"
```

---

### Task 3: Hook passes the session cwd + install-doc note

**Files:**
- Modify: `hooks/scripts/session-start.sh`, `docs/install.md`
- Modify: `tests/adapter/test_session_start_hook.py` (append)

**Interfaces:**
- Consumes: the SessionStart stdin payload's `cwd` field (see `docs/research/2026-08-11-claude-code-plugin-wiring.md`).
- Produces: the hook reads stdin (currently discarded), extracts `cwd` tolerantly via python3 (garbage/absent → empty), and invokes `"$ROOT/bin/mneme" context --cwd "<cwd>"` when non-empty, plain `context` otherwise. Same exit-0-everywhere contract, same single-line `hookSpecificOutput` JSON. Stdin must be consumed via a temp file exactly like `distill-hook.sh` does (large payloads; READ that script and mirror its mechanism). `docs/install.md` gains a short "First open in a knowledge repo" paragraph describing the ask-to-register behavior.

- [ ] **Step 1: Write the failing tests**

Append to `tests/adapter/test_session_start_hook.py`:

```python
def test_cwd_detection_nudges_registration(tmp_path):
    home = tmp_path / "home"
    kb = tmp_path / "team-kb"
    kb.mkdir()
    (kb / "MNEME.md").write_text("# scope\n", encoding="utf-8")
    env = dict(os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        input=json.dumps({"cwd": str(kb), "source": "startup"}),
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Unregistered knowledge repo detected" in ctx
    assert "team-kb" in ctx


def test_payload_without_cwd_still_injects_brief(tmp_path):
    home = tmp_path / "home"
    env = dict(os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
    result = subprocess.run(
        ["bash", str(SCRIPT)], input="not json at all",
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "mneme noticing" in ctx
    assert "Unregistered" not in ctx
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/adapter/test_session_start_hook.py -v`
Expected: the new cwd test FAILS (no nudge — stdin is discarded today); existing tests stay green.

- [ ] **Step 3: Modify the hook and docs**

In `hooks/scripts/session-start.sh`, before the `mneme context` invocation, consume stdin to a trap-cleaned temp file and extract `cwd` (mirroring `distill-hook.sh`'s payload-file mechanism, tolerant of garbage):

```bash
PAYLOAD_FILE="$(mktemp)"
trap 'rm -f "$PAYLOAD_FILE"' EXIT
cat > "$PAYLOAD_FILE" 2>/dev/null || true
SESSION_CWD="$(MNEME_HOOK_PAYLOAD_FILE="$PAYLOAD_FILE" python3 - <<'PY' 2>/dev/null || true
import json
import os

try:
    with open(os.environ["MNEME_HOOK_PAYLOAD_FILE"], encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    raise SystemExit(0)
print(data.get("cwd", ""))
PY
)"
if [ -n "$SESSION_CWD" ]; then
  OUT="$("$ROOT/bin/mneme" context --cwd "$SESSION_CWD" 2>/dev/null)" || exit 0
else
  OUT="$("$ROOT/bin/mneme" context 2>/dev/null)" || exit 0
fi
```

(replacing the current single `OUT=` line; everything after is unchanged). Add to `docs/install.md`, after the hook-behavior section:

```markdown
### First open in a knowledge repo

Opening a session inside a repo that carries a `MNEME.md` but is not yet
registered makes the session-start brief ask whether you want to register it —
one confirmation wires up `mneme registry add` (using the repo's origin URL
when it has one) and offers `/mneme:adopt` if governance files are missing.
Declining is respected for the session.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/adapter/test_session_start_hook.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add hooks/scripts/session-start.sh docs/install.md tests/adapter/test_session_start_hook.py
git commit -m "feat: session-start hook asks to register detected knowledge repos"
```

---

### Task 4: Release 0.3.0

**Files:**
- Modify: `core/mneme_core/__init__.py`, `core/mneme_index/__init__.py`, `.claude-plugin/plugin.json`, `pyproject.toml`, `tests/e2e/test_release.py`, `CHANGELOG.md`

**Interfaces:**
- Produces: version `0.3.0` in all four locations (the one-repo-one-version rule), the release-test literal updated, and a `## 0.3.0 — 2026-08-12` CHANGELOG entry describing the session-start knowledge-repo detection feature.

- [ ] **Step 1: Update the failing pin**

Change `tests/e2e/test_release.py`'s `assert mneme_core.__version__ == "0.2.1"` to `"0.3.0"`.
Run: `python3 -m pytest tests/e2e/test_release.py -v` — Expected: FAIL (version still 0.2.1, CHANGELOG lacks the heading).

- [ ] **Step 2: Apply the bump**

Set `0.3.0` in `core/mneme_core/__init__.py`, `core/mneme_index/__init__.py`, `.claude-plugin/plugin.json`, `pyproject.toml`. Add atop CHANGELOG's release list:

```markdown
## 0.3.0 — 2026-08-12

- **Session-start knowledge-repo detection** — opening a session inside an
  unregistered repo that carries a `MNEME.md` makes the injected brief ask the
  user whether to register it with the local mneme (origin URL pre-filled,
  `/mneme:adopt` offered when governance files are missing, declines
  respected). Detection is deterministic (`routing.find_knowledge_repo`) and
  can never break session start.
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `python3 -m pytest tests/e2e/test_release.py -v` → all PASS.

- [ ] **Step 4: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/__init__.py core/mneme_index/__init__.py .claude-plugin/plugin.json pyproject.toml tests/e2e/test_release.py CHANGELOG.md
git commit -m "release: 0.3.0"
```

---

## Verification (end of plan)

1. `python3 -m pytest -v` — entire suite green.
2. Live-shaped smoke:
   ```bash
   export MNEME_HOME=$(mktemp -d) CLAUDE_PLUGIN_ROOT="$PWD"
   T=$(mktemp -d) && mkdir "$T/team-kb" && printf '# s\n' > "$T/team-kb/MNEME.md"
   printf '{"cwd": "%s", "source": "startup"}' "$T/team-kb" | bash hooks/scripts/session-start.sh \
     | python3 -c "import json,sys; ctx=json.load(sys.stdin)['hookSpecificOutput']['additionalContext']; assert 'Unregistered knowledge repo detected' in ctx; print('nudge ok')"
   bin/mneme registry add team-kb --repo local:"$T/team-kb" --path "$T/team-kb"
   printf '{"cwd": "%s"}' "$T/team-kb" | bash hooks/scripts/session-start.sh \
     | python3 -c "import json,sys; ctx=json.load(sys.stdin)['hookSpecificOutput']['additionalContext']; assert 'Unregistered' not in ctx; print('registered ok')"
   bin/mneme --version   # 0.3.0
   ```
3. `claude plugin validate . --strict` — exit 0.
4. `git log --oneline` shows one commit per task (4 new commits).
