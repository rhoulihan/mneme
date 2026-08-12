# Mneme Plan 07 — End-to-End Harness, CI, Dogfood, Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out v0.2.0: one end-to-end test that exercises the entire loop through the real binaries and hook scripts, engine-repo CI, the dogfood seed (real knowledge from building mneme, as distiller proposals that must clear the real gate), public-repo hygiene (CONTRIBUTING, SECURITY), and the release bump.

**Architecture:** The e2e test chains every phase through `subprocess` against the actual `bin/` and `hooks/scripts/` entry points — no in-process shortcuts — with a shimmed `claude` and a local bare remote, proving the seams between plans hold. The dogfood seed lives as a proposals document (`docs/dogfood/seed-proposals.json`) whose test requirement is that the REAL ingest gate stages every unit with zero rejections and zero quarantines — the knowledge is genuine lessons from this build. (Publishing the resulting `mneme-dev-knowledge` repo to GitHub happens after merge, outside the workflow — workers never push.)

**Tech Stack:** No new dependencies.

**Spec:** §11 (v1 ships list — this completes it), §7 end-to-end.

## Global Constraints

- All prior Global Constraints hold; the full suite (Plan 06's count) stays green after every task.
- The e2e test uses only scratch homes (`mktemp`-style tmp dirs), a shimmed `claude`, and local bare remotes — no network, no real `claude`, and it must leave nothing outside pytest tmp dirs.
- Dogfood seed units must be REAL knowledge from this repository's build history (plans, audit findings, wiring reference) — no filler. Each must clear the promotion rule on its face: verified in this build, named failure pattern, non-obvious.
- Run tests with `python3 -m pytest` from the repo root.

## File Structure

```
tests/e2e/test_full_loop.py        # Task 1
.github/workflows/ci.yml           # Task 2
docs/dogfood/seed-proposals.json   # Task 3
tests/e2e/test_dogfood_seed.py     # Task 3
CONTRIBUTING.md                    # Task 4
SECURITY.md                        # Task 4
tests/e2e/test_repo_hygiene.py     # Task 4
CHANGELOG.md                       # Task 5
core/mneme_core/__init__.py        # Task 5 (0.2.0)
.claude-plugin/plugin.json         # Task 5 (0.2.0)
README.md                          # Task 5 (Phase 07 row only)
tests/e2e/test_release.py          # Task 5
```

---

### Task 1: The full-loop e2e test

**Files:**
- Create: `tests/e2e/test_full_loop.py`

**Interfaces:**
- Consumes: everything — `bin/mneme`, `bin/mneme-index`, `hooks/scripts/session-start.sh`, `hooks/scripts/distill-hook.sh`, `bin/mneme-distill-pipeline`, a shim `claude`, a bare remote.
- Produces: one test module proving the loop end to end: scaffold → hook injection → flag → background distill (foreground mode, shim) → machine gate (one clean unit staged, one secret-bearing unit quarantined) → share apply → PR-mode branch on the remote with provenance trailer → staging drained → status reflects it all → index rebuild → search finds the harvested knowledge in the knowledge repo.

- [ ] **Step 1: Write the failing test**

Create `tests/e2e/test_full_loop.py`:

```python
"""The whole loop, through the real entry points (spec §7 end to end)."""
import json
import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PROPOSALS = {
    "proposals": [
        {
            "type": "skill", "edit": "new", "target": "e2e-knowledge",
            "name": "deploy-widget", "description": "Use when deploying the widget service after a failed cutover",
            "procedure": "1. Run preflight.\n2. Blue-green cutover.",
            "failure_pattern": "Naive restart loops forever; the LB caches the dead target.",
            "confidence": 0.9, "rationale": "verified this session",
        },
        {
            "type": "fact", "edit": "new", "target": "e2e-knowledge", "topic": "staging-env",
            "category": "gotcha", "text": "The staging key is AKIAIOSFODNN7EXAMPLE",
            "tags": ["staging"], "confidence": 0.4, "rationale": "seen once",
        },
    ]
}


def sh(env, *args, stdin=None, cwd=None):
    return subprocess.run(
        list(args), input=stdin, capture_output=True, text=True, env=env,
        cwd=str(cwd) if cwd else None,
    )


def test_full_loop(tmp_path):
    home = tmp_path / "home"
    env = dict(
        os.environ,
        MNEME_HOME=str(home),
        CLAUDE_PLUGIN_ROOT=str(REPO_ROOT),
        MNEME_DISTILL_FOREGROUND="1",
    )
    env.pop("MNEME_DISTILLING", None)
    mneme = str(REPO_ROOT / "bin" / "mneme")

    # claude shim: returns the canned distiller output
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    shim = bindir / "claude"
    shim.write_text(
        "#!/bin/sh\necho '" + json.dumps({"result": json.dumps(PROPOSALS)}) + "'\n",
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    env["MNEME_CLAUDE_BIN"] = str(shim)

    # 1. scaffold + register
    r = sh(env, mneme, "new", "e2e-knowledge", "--owner", "e2e-team")
    assert r.returncode == 0, r.stderr
    kb = json.loads((home / "registry.json").read_text(encoding="utf-8"))["plugins"][0]["path"]

    # 2. wire a bare remote
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "-C", kb, "remote", "add", "origin", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "-C", kb, "push", "-u", "origin", "main"], check=True, capture_output=True)

    # 3. session-start hook injects the brief
    r = sh(env, "bash", str(REPO_ROOT / "hooks" / "scripts" / "session-start.sh"), stdin="{}")
    assert r.returncode == 0
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "e2e-knowledge" in ctx

    # 4. flag, then the Stop hook distills through the shim
    assert sh(env, mneme, "flag", "solved the widget cutover after two dead ends").returncode == 0
    r = sh(
        env, "bash", str(REPO_ROOT / "hooks" / "scripts" / "distill-hook.sh"),
        stdin=json.dumps({"transcript_path": "/tmp/e2e.jsonl", "stop_hook_active": False}),
    )
    assert r.returncode == 0

    # 5. machine gate: clean skill staged, secret-bearing fact quarantined, flags consumed
    r = sh(env, mneme, "stage", "list", "--all")
    assert "skill/new" in r.stdout and "staged" in r.stdout
    assert "quarantined" in r.stdout
    assert sh(env, mneme, "distill", "pending").returncode == 1

    # 6. human gate: approve the staged skill only
    staged_id = next(
        line.split()[0]
        for line in sh(env, mneme, "stage", "list").stdout.splitlines()
        if line.strip()
    )
    r = sh(env, mneme, "share", "apply", "--ids", staged_id)
    assert r.returncode == 0, r.stderr
    assert "harvested e2e-knowledge: 1 units" in r.stdout

    # 7. the harvest branch reached the remote with provenance
    branches = subprocess.run(
        ["git", "-C", str(remote), "branch"], capture_output=True, text=True, check=True
    ).stdout
    harvest_branch = next(b.strip() for b in branches.splitlines() if "mneme/harvest-" in b)
    message = subprocess.run(
        ["git", "-C", str(remote), "log", harvest_branch, "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "knowledge: harvest" in message
    assert "Mneme-Source:" in message

    # 8. staging drained (quarantined item remains, staged one gone)
    assert sh(env, mneme, "stage", "list").stdout.strip() == ""

    # 9. status reflects the run
    out = sh(env, mneme, "status").stdout
    assert "plugins: 1 registered" in out
    assert "submissions: 1 recorded" in out

    # 10. merge the harvest into the kb main and confirm retrieval finds it
    subprocess.run(["git", "-C", kb, "merge", harvest_branch.strip()], check=True, capture_output=True)
    assert sh(env, mneme, "index", "rebuild").returncode == 0
    r = sh(env, mneme, "search", "cutover widget")
    assert "skills/deploy-widget" in r.stdout
```

- [ ] **Step 2: Run the test to verify current behavior**

Run: `python3 -m pytest tests/e2e/test_full_loop.py -v`
Expected: PASS is possible (this is an integration test over completed plans) — but treat any failure as a REAL seam defect: diagnose and fix the product code (never the test's intent), recording each fix as a deviation. If it passes first try, that is the finding: record "seams held" in notes.

- [ ] **Step 3: Fix any seam defects found**

Apply minimal product-code fixes for anything Step 2 surfaced, with the full suite green after each.

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_full_loop.py
git commit -m "test: end-to-end loop through real entry points"
```

(Include any product-code fixes from Step 3 in this commit and name them in the commit body.)

---

### Task 2: Engine CI

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: GitHub Actions CI for the engine repo: pytest on Python 3.10 and 3.12, plus `bin/mneme lint .` (the engine's own skills must satisfy the engine's own linter — dogfood in CI).

- [ ] **Step 1: Write the failing check**

There is no pytest surface for a workflow file; the gate is structural. Create the file in Step 2 and validate: `python3 -c "import json,sys,pathlib; import yaml" 2>/dev/null` — PyYAML is not a dependency, so validate shape by running the two commands locally instead:

Run: `python3 -m pytest && bin/mneme lint .`
Expected: both exit 0 BEFORE adding CI (they are what CI will run). If `bin/mneme lint .` fails on the engine repo, fix the offending skill files first — that is the point of the check.

- [ ] **Step 2: Create `.github/workflows/ci.yml`**

```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.10", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "${{ matrix.python }}"
      - name: Install dev dependency
        run: python -m pip install pytest
      - name: Test suite
        run: python -m pytest
      - name: Lint our own skills with our own linter
        run: bin/mneme lint .
```

- [ ] **Step 3: Re-run the local equivalents**

Run: `python3 -m pytest && bin/mneme lint .` → both exit 0.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: pytest matrix and self-lint"
```

---

### Task 3: Dogfood seed — real knowledge from building mneme

**Files:**
- Create: `docs/dogfood/seed-proposals.json`, `tests/e2e/test_dogfood_seed.py`

**Interfaces:**
- Produces: a proposals document holding genuine, non-obvious knowledge units from this build, in the exact schema the distiller emits — 3 skills and 6 facts. The test proves the REAL gate accepts all of them: ingest into a scratch home stages 9 candidates, 0 quarantined, 0 rejected. This document seeds the public `mneme-dev-knowledge` plugin after merge.

- [ ] **Step 1: Write the failing test**

Create `tests/e2e/test_dogfood_seed.py`:

```python
import json
from pathlib import Path

from mneme_core import staging
from mneme_core.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED = REPO_ROOT / "docs" / "dogfood" / "seed-proposals.json"


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_seed_document_is_valid_and_substantive():
    data = json.loads(SEED.read_text(encoding="utf-8"))
    entries = data["proposals"]
    assert len(entries) == 9
    kinds = [e["type"] for e in entries]
    assert kinds.count("skill") == 3
    assert kinds.count("fact") == 6
    for e in entries:
        assert e["rationale"], e
        if e["type"] == "skill":
            assert "failure" in json.dumps(e).lower() or e["failure_pattern"], e


def test_seed_clears_the_real_gate(tmp_path, capsys):
    home = tmp_path / "home"
    code, out, _ = run(
        capsys, "--home", str(home), "distill", "ingest", str(SEED),
        "--source", "mneme-build@plans-01-07",
    )
    assert code == 0
    assert "staged 9" in out
    assert "quarantined 0" in out
    assert "rejected 0" in out
    cands = staging.load_candidates(home)
    assert len(cands) == 9
    assert all(c.target == "mneme-dev-knowledge" for c in cands)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/e2e/test_dogfood_seed.py -v`
Expected: FAIL — seed file missing.

- [ ] **Step 3: Write `docs/dogfood/seed-proposals.json`**

Nine units, target `mneme-dev-knowledge`, every one earned in this build. Use exactly this content:

```json
{
  "proposals": [
    {
      "type": "skill", "edit": "new", "target": "mneme-dev-knowledge",
      "name": "record-executable-bits-on-drvfs",
      "description": "Use when committing executable scripts from a WSL checkout on a Windows drvfs mount (/mnt/c) and the executable bit must reach git",
      "procedure": "1. chmod +x the file as usual (harmless, but insufficient on drvfs).\n2. Record the bit directly in the index: `git update-index --add --chmod=+x <file>`.\n3. Verify with `git ls-files --stage <file>` — expect mode 100755 before committing.",
      "failure_pattern": "chmod alone silently does nothing on drvfs mounts: the file commits as 100644, and launchers fail with 'Permission denied' only for users cloning to real POSIX filesystems — the author never sees the breakage.",
      "confidence": 0.95, "rationale": "hit on every plan of this build; verified by inspecting index modes"
    },
    {
      "type": "skill", "edit": "new", "target": "mneme-dev-knowledge",
      "name": "cross-module-audit-after-per-task-review",
      "description": "Use when orchestrating multi-agent implementation with per-task review and deciding whether a final whole-branch audit is worth the cost",
      "procedure": "1. Keep per-task adversarial review (it keeps implementers honest — near-zero fix rounds).\n2. After all tasks land, run two independent whole-branch auditors: one attacking correctness across module boundaries, one checking plan/spec compliance.\n3. Give auditors concrete attack lanes drawn from the plan's trust boundaries, and require demonstrated findings only.\n4. Route blocking findings through one fix pass plus an independent re-check.",
      "failure_pattern": "Per-task review structurally cannot see cross-module composition bugs: in this build it missed a staging-store frontmatter injection, a date-in-hash dedup defeat, and a whole-file rewrite masquerading as a delta edit — every one was caught by the whole-branch audit instead.",
      "confidence": 0.9, "rationale": "audit layer found 13+ demonstrated defects across five plans that per-task review approved"
    },
    {
      "type": "skill", "edit": "new", "target": "mneme-dev-knowledge",
      "name": "two-phase-llm-gate",
      "description": "Use when an LLM must contribute content to a persistent store and you need the store protected from model failure modes",
      "procedure": "1. Split the flow: a 'prepare' step assembles the prompt bundle deterministically; the LLM returns STRUCTURED PROPOSALS only; an 'ingest' step validates, renders canonically, scans, dedups, and stages in tested code.\n2. Treat every proposal field as untrusted input: enum/length/shape validation, size caps, secret scan, canonical rendering by construction.\n3. Hash on semantic content (strip your own stamps like dates and session labels) so decline/dedup ledgers survive across days and sessions.",
      "failure_pattern": "Letting the model write the store directly invites context collapse and format drift; even with proposals, hashing date-stamped renderings meant declined items resurfaced the next day with fresh hashes until semantic hashing landed.",
      "confidence": 0.9, "rationale": "distiller architecture of this repo; both failure modes demonstrated by audit before fixes"
    },
    {
      "type": "fact", "edit": "new", "target": "mneme-dev-knowledge", "topic": "python-stdlib",
      "category": "constraint",
      "text": "CPython's bundled SQLite ships with FTS5 enabled on mainstream Linux, macOS, and Windows builds, so full-text search needs no third-party dependency",
      "tags": ["sqlite", "python"], "confidence": 0.9,
      "rationale": "mneme-index runs FTS5 through stdlib sqlite3 across the test matrix"
    },
    {
      "type": "fact", "edit": "new", "target": "mneme-dev-knowledge", "topic": "python-packaging",
      "category": "gotcha",
      "text": "Ubuntu marks the system Python externally managed (PEP 668), so pip install --user fails until you pass --break-system-packages or use a venv",
      "tags": ["python", "ubuntu"], "confidence": 0.9,
      "rationale": "hit installing pytest on this machine"
    },
    {
      "type": "fact", "edit": "new", "target": "mneme-dev-knowledge", "topic": "python-stdlib",
      "category": "gotcha",
      "text": "argparse's action=version raises SystemExit, which escapes an in-process main() under test — implement version flags as store_true when you test CLIs in-process",
      "tags": ["python", "argparse", "testing"], "confidence": 0.9,
      "rationale": "caught in Plan 01 self-review before execution"
    },
    {
      "type": "fact", "edit": "new", "target": "mneme-dev-knowledge", "topic": "python-stdlib",
      "category": "gotcha",
      "text": "SQLite URI filenames treat hash, question mark, and percent specially — percent-encode paths before building file: URIs or mode=ro silently drops and the wrong file opens",
      "tags": ["sqlite", "security"], "confidence": 0.9,
      "rationale": "demonstrated by the Plan 02 audit: a # in the path voided the read-only guarantee"
    },
    {
      "type": "fact", "edit": "new", "target": "mneme-dev-knowledge", "topic": "claude-code-platform",
      "category": "constraint",
      "text": "Claude Code hook stdout is injected as context only for SessionStart, UserPromptSubmit, and UserPromptExpansion; other events log stdout without showing it",
      "tags": ["claude-code", "hooks"], "confidence": 0.9,
      "rationale": "verified against code.claude.com hook docs and shipping plugins"
    },
    {
      "type": "fact", "edit": "new", "target": "mneme-dev-knowledge", "topic": "claude-code-platform",
      "category": "gotcha",
      "text": "string.Template substitution into JSON templates breaks on quote-bearing values — JSON-escape each value (json.dumps of the string, shorn of quotes) before substituting",
      "tags": ["python", "templates"], "confidence": 0.9,
      "rationale": "Plan 03 audit demonstrated broken manifests from a quoted --description"
    }
  ]
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/e2e/test_dogfood_seed.py -v` → all PASS. (If ingest rejects or quarantines any unit, fix the SEED CONTENT to clear the real gate — the gate is authoritative, not the seed.)

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add docs/dogfood/seed-proposals.json tests/e2e/test_dogfood_seed.py
git commit -m "feat: dogfood seed — build lessons as distiller proposals"
```

---

### Task 4: Public-repo hygiene — CONTRIBUTING and SECURITY

**Files:**
- Create: `CONTRIBUTING.md`, `SECURITY.md`, `tests/e2e/test_repo_hygiene.py`

**Interfaces:**
- Produces: `CONTRIBUTING.md` for the ENGINE repo (distinct from the knowledge-repo template): the spec-first/plan-driven process, how to run the suite, the strict-TDD expectation, where plans live, the rule that every PR keeps the suite green and skills lint-clean; `SECURITY.md`: how to report vulnerabilities privately (GitHub security advisories on rhoulihan/mneme), what counts as a security issue here (gate bypasses, secret-scan evasion, read-only query escapes, hook injection).

- [ ] **Step 1: Write the failing test**

Create `tests/e2e/test_repo_hygiene.py`:

```python
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_contributing_covers_process():
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for token in ("python3 -m pytest", "docs/superpowers/plans", "bin/mneme lint"):
        assert token in text, token


def test_security_covers_reporting_and_scope():
    text = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "security advisor" in text.lower()
    for token in ("secret", "read-only", "hook"):
        assert token in text.lower(), token
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/e2e/test_repo_hygiene.py -v`
Expected: FAIL — files missing.

- [ ] **Step 3: Write both documents**

Write `CONTRIBUTING.md` and `SECURITY.md` fully, covering everything the Interfaces block lists, in the repo's established voice (see README). CONTRIBUTING must explain: the spec (`docs/superpowers/specs/`) is authoritative; changes land through implementation plans (`docs/superpowers/plans/`) with test-first tasks; every PR runs `python3 -m pytest` and `bin/mneme lint .` green; knowledge contributions belong in knowledge plugins, not this repo. SECURITY must name the private reporting channel (GitHub security advisories), response expectations, and in-scope classes: machine-gate bypasses (scan/dedup/declined-ledger evasion), `mneme db query` read-only escapes, hook-context injection, scaffold template injection.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/e2e/test_repo_hygiene.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add CONTRIBUTING.md SECURITY.md tests/e2e/test_repo_hygiene.py
git commit -m "docs: contributing process and security policy"
```

---

### Task 5: Release 0.2.0

**Files:**
- Modify: `core/mneme_core/__init__.py`, `.claude-plugin/plugin.json`, `README.md` (Phase 07 row only)
- Create: `CHANGELOG.md`, `tests/e2e/test_release.py`

**Interfaces:**
- Produces: version `0.2.0` everywhere it lives, consistency-tested; a `CHANGELOG.md` with an `## 0.2.0` section summarizing phases 01–07 in a few lines each and an `## 0.1.0` stub for the foundation; README Phase 07 row flipped to `✅ merged`.

- [ ] **Step 1: Write the failing test**

Create `tests/e2e/test_release.py`:

```python
import json
from pathlib import Path

import mneme_core

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_version_consistency():
    assert mneme_core.__version__ == "0.2.0"
    manifest = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == mneme_core.__version__
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.lstrip().startswith("# Changelog")
    assert f"## {mneme_core.__version__}" in changelog


def test_readme_status_complete():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "🔨 in progress" not in readme
    assert "📝 planned" not in readme
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/e2e/test_release.py -v`
Expected: FAIL — version is 0.1.0, CHANGELOG missing.

- [ ] **Step 3: Apply the release**

Bump `__version__` and the manifest `version` to `0.2.0`. Write `CHANGELOG.md` (heading `# Changelog`, then `## 0.2.0 — 2026-08-11` covering: retrieval index, scaffold factory, distiller machine gate, harvest/PR pipeline, Claude Code adapter, dogfood seed, plus the notable audit-driven hardening: semantic hashing, SELECT-only queries, URI encoding, atomic harvest rollback; then `## 0.1.0 — 2026-08-11` for the foundation). Flip README's Phase 07 row to `✅ merged` (and Phase 05/06 rows if the Plan 06 task left them stale — the test enforces the end state).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/e2e/test_release.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/__init__.py .claude-plugin/plugin.json CHANGELOG.md README.md tests/e2e/test_release.py
git commit -m "release: 0.2.0"
```

---

### Task 6: Carried Plan 06 minors — strict validation, large payloads, status resilience

**Files:**
- Modify: `.claude-plugin/marketplace.json`, `hooks/scripts/distill-hook.sh`, `core/mneme_core/cli.py` (`_status_cmd`)
- Create: `tests/e2e/test_carried_minors.py`

**Interfaces:**
- Produces three closures (READ each current file first — the Plan 06 audit fix already reshaped the pipeline's flag handling and `distill pending`'s corrupt-line behavior; integrate, don't clobber):
  1. `marketplace.json` gains a top-level `"description"` so `claude plugin validate . --strict` passes clean.
  2. `distill-hook.sh` hands the stdin payload to python3 via a `mktemp` temp file (trap-cleaned) instead of an environment variable — Stop payloads carry `last_assistant_message` and can exceed Linux's per-string exec limit (~128KB), which would make the hook fail exactly on long sessions. Behavior otherwise unchanged (same guards, same outputs).
  3. `mneme status` degrades gracefully per line: corrupt/truncated lines in `flags.jsonl` or `submitted.jsonl` are skipped (counted lines only include parseable records), a trailing note `warning: N unreadable line(s) skipped` is printed when any were, and exit stays 0. No raw tracebacks on any input.

- [ ] **Step 1: Write the failing tests**

Create `tests/e2e/test_carried_minors.py`:

```python
import json
import os
import subprocess
from pathlib import Path

from mneme_core import flags, paths
from mneme_core.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_marketplace_has_description():
    data = json.loads(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert data.get("description")


def test_distill_hook_survives_huge_payload(tmp_path):
    home = tmp_path / "home"
    payload = json.dumps(
        {
            "transcript_path": "/tmp/t.jsonl",
            "stop_hook_active": True,
            "last_assistant_message": "x" * 300_000,
        }
    )
    env = dict(
        os.environ,
        MNEME_HOME=str(home),
        CLAUDE_PLUGIN_ROOT=str(REPO_ROOT),
        MNEME_DISTILL_FOREGROUND="1",
    )
    env.pop("MNEME_DISTILLING", None)
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "hooks" / "scripts" / "distill-hook.sh")],
        input=payload, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0
    # guard held despite the size: nothing distilled (stop_hook_active)


def test_status_survives_corrupt_ledgers(tmp_path, capsys):
    home = tmp_path / "home"
    flags.add_flag(home, "good flag")
    with paths.flags_path(home).open("a", encoding="utf-8") as f:
        f.write("{corrupt json\n")
    paths.ensure_layout(home)
    with paths.submitted_path(home).open("a", encoding="utf-8") as f:
        f.write('{"target": "ok-kb", "branch": "b", "units": ["u"]}\n')
        f.write("also not json\n")
    code, out, _ = run(capsys, "--home", str(home), "status")
    assert code == 0
    assert "flags: 1 pending" in out
    assert "submissions: 1 recorded" in out
    assert "unreadable line" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/e2e/test_carried_minors.py -v`
Expected: FAIL — no marketplace description; the huge-payload hook run fails or errors (exec limit); status crashes or miscounts on corrupt lines.

- [ ] **Step 3: Implement the three fixes**

1. Add to `.claude-plugin/marketplace.json`, after `"name"`: `"description": "Knowledge-mining engine for AI coding agents — the plugin is the memory.",`
2. In `distill-hook.sh`, replace the env-var handoff with a temp file:

```bash
PAYLOAD_FILE="$(mktemp)"
trap 'rm -f "$PAYLOAD_FILE"' EXIT
cat > "$PAYLOAD_FILE" 2>/dev/null || true
TRANSCRIPT="$(MNEME_HOOK_PAYLOAD_FILE="$PAYLOAD_FILE" python3 - <<'PY' 2>/dev/null || true
import json
import os

try:
    with open(os.environ["MNEME_HOOK_PAYLOAD_FILE"], encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    raise SystemExit(1)
if data.get("stop_hook_active"):
    raise SystemExit(1)
print(data.get("transcript_path", ""))
PY
)"
```

(keep every guard and the rest of the script exactly as it stands post-audit-fix).
3. In `_status_cmd`, wrap the per-line JSON parses of both ledgers in try/except, count skipped lines across both, and print `warning: N unreadable line(s) skipped` when N > 0. If the Plan 06 audit fix already made `flags.read_flags` tolerant, reuse its mechanism rather than duplicating; the requirement is the observable behavior in the test.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/e2e/test_carried_minors.py -v` → all PASS. Also run: `claude plugin validate . --strict` → exits 0 (record verbatim output).

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add .claude-plugin/marketplace.json hooks/scripts/distill-hook.sh core/mneme_core/cli.py tests/e2e/test_carried_minors.py
git commit -m "fix: strict-clean marketplace, large hook payloads, resilient status"
```

---

## Verification (end of plan)

1. `python3 -m pytest -v` — entire suite green, including the new e2e modules.
2. `bin/mneme lint .` — exit 0 (CI's self-lint holds).
3. `bin/mneme --version` → `0.1.0`? NO — must print `0.2.0`.
4. Re-run the e2e loop once more from a clean scratch home (pytest `tests/e2e/test_full_loop.py -v`) to confirm no state leakage between runs.
5. `git log --oneline` shows one commit per task (6 new commits).

## After merge (session work, not workflow work)

- Scaffold and publish the real dogfood plugin: `bin/mneme new mneme-dev-knowledge`, `mneme distill ingest docs/dogfood/seed-proposals.json`, `mneme share apply` (commit mode), `gh repo create rhoulihan/mneme-dev-knowledge`, push — done by the session (network + gh), never by workflow agents.
- Tag `v0.2.0` and push the tag.
