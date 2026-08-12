# Mneme Plan 05 — Harvest: Apply, Commit, PR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The human-gate half of the pipeline (spec §7.3–7.5): applying approved candidates to knowledge-repo clones as delta edits, the git/branch/commit/push/PR plumbing with provenance trailers, the `mneme share` and `mneme decline` commands the harvesting skill will drive, the submissions ledger, and the `mneme verify` staleness sweep.

**Architecture:** Two new modules. `gitops.py` wraps every git side effect in `subprocess` calls against the knowledge repo's local clone — tested against real temporary git repos with `git init --bare` directories standing in as remotes (local pushes need no network), and a fake `gh` shim on `PATH` standing in for GitHub. `harvest.py` turns candidates into tree edits: skills are unit-granular file writes; fact bullets are line-granular inserts/replacements keyed by topic-key (delta edits only — no file regeneration except the sanctioned mechanical `knowledge-index` rebuild). The CLI surface is deliberately non-interactive — `share list / diff / apply`, `decline` — because the conversational review UX belongs to the Plan 06 harvesting skill, which calls these commands with explicit ids after the human approves. Nothing here auto-pushes: `apply` touches the network only when a remote exists and the repo's mode says so.

**Tech Stack:** Python ≥3.10 stdlib (`subprocess`, `difflib`, `shutil`, `datetime`). Dev-only: `pytest`. Requires `git` ≥2.28; `gh` optional at runtime (its absence degrades to printed instructions).

**Spec:** §7.3 (harvest gate mechanics), §7.4 (repo-side re-validation before the PR), §7.5 (status surface, staleness sweep), §8 (provenance trailers, no auto-push), §4.2 (modes `pr`/`commit`). Builds on Plans 01–04 (`staging` incl. metadata fields, `scan`, `lint`, `registry`, `routing`, `scaffold.regenerate_index_skill`, `mneme distill ingest`).

## Global Constraints

- All Plan 01–04 Global Constraints still apply. Existing suite stays green after every task.
- **No auto-push, ever:** network side effects (push, `gh pr create`) happen only inside `share apply`, only for repos whose registered mode requires them, only when a remote named `origin` exists — and `--dry-run` must never touch the tree, git, or the network.
- All git subprocess calls set explicit identity fallbacks (`-c user.name=mneme -c user.email=mneme@localhost`) and `capture_output=True`; failures raise `MnemeError` with a stderr excerpt (≤300 chars).
- Delta edits only: fact applies modify exactly one bullet line (plus file creation for new topics); skill applies write exactly one `skills/<name>/SKILL.md`. The only whole-file regeneration is `scaffold.regenerate_index_skill`.
- Harvest re-validates before committing: `lint_repo` must show no error-severity issues and every applied body must re-scan clean — the repo is restored (`git checkout -- .` + `git clean -fd` scoped to the repo) and the batch aborts with `MnemeError` otherwise.
- Tests never require network or a real `gh`: bare-directory remotes and a `PATH`-shimmed fake `gh` script cover those paths.
- Run tests with `python3 -m pytest` from the repo root.

## File Structure

```
core/mneme_core/
├── paths.py       # Task 1: submitted_path()
├── gitops.py      # Tasks 1–3 (new)
├── harvest.py     # Tasks 4–6 (new)
└── cli.py         # Tasks 7–9: share list/diff/apply, decline, verify
tests/core/
├── test_gitops_basic.py     # Task 1
├── test_gitops_commit.py    # Task 2
├── test_gitops_pr.py        # Task 3
├── test_harvest_skills.py   # Task 4
├── test_harvest_facts.py    # Task 5
├── test_harvest_batch.py    # Task 6
├── test_cli_share_view.py   # Task 7
├── test_cli_share_apply.py  # Task 8
└── test_cli_verify.py       # Task 9
```

**Shared test helper** (repeated verbatim where needed): a scaffolded knowledge plugin with one staged skill candidate and one staged fact candidate, produced through the real pipeline (`scaffold.create` + `distill ingest` fixtures), so harvest tests exercise the true formats.

```python
import json

from mneme_core import scaffold
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def seed(tmp_path, capsys, name="acme-knowledge", mode="pr"):
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo", mode=mode)
    props = {
        "proposals": [
            {
                "type": "skill", "edit": "new", "target": name,
                "name": "deploy-widget", "description": "Use when deploying the widget service",
                "procedure": "1. preflight\n2. cutover",
                "failure_pattern": "restart loop hits the LB cache",
                "confidence": 0.9, "rationale": "verified",
            },
            {
                "type": "fact", "edit": "new", "target": name, "topic": "staging-env",
                "category": "constraint", "text": "Staging DB resets nightly at 04:00 UTC",
                "tags": ["staging"], "confidence": 0.8, "rationale": "observed",
            },
        ]
    }
    p = tmp_path / "props.json"
    p.write_text(json.dumps(props), encoding="utf-8")
    run(capsys, "--home", str(home), "distill", "ingest", str(p), "--source", "demo@s1")
    return home, target
```

---

### Task 1: Git plumbing basics (`gitops.py`, part 1) + ledger path

**Files:**
- Modify: `core/mneme_core/paths.py`
- Create: `core/mneme_core/gitops.py`, `tests/core/test_gitops_basic.py`

**Interfaces:**
- Consumes: `MnemeError`, `subprocess`.
- Produces: `paths.submitted_path(home) -> Path` (`home / "submitted.jsonl"`); `gitops.git(repo: Path, *args: str) -> str` — runs git with identity fallbacks, returns stripped stdout, `MnemeError` (stderr excerpt) on nonzero exit; `is_git_repo(repo) -> bool`; `is_clean(repo) -> bool` (`status --porcelain` empty); `current_branch(repo) -> str`; `has_remote(repo) -> bool` (an `origin` remote exists); `sync_main(repo) -> None` — checkout `main`, and `pull --ff-only origin main` only when a remote exists; `create_branch(repo, name) -> None` (`checkout -b`); `restore(repo) -> None` — `checkout -- .` then `clean -fd` (repo-scoped hard reset of uncommitted work).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_gitops_basic.py`:

```python
import subprocess

import pytest

from mneme_core import gitops, paths
from mneme_core.errors import MnemeError


def make_repo(root):
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(root),
         "add", "-A"], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(root),
         "commit", "-m", "seed"], check=True, capture_output=True,
    )
    return root


def test_submitted_path(tmp_path):
    assert paths.submitted_path(tmp_path) == tmp_path / "submitted.jsonl"


def test_git_returns_stdout_and_raises_on_failure(tmp_path):
    repo = make_repo(tmp_path / "r")
    assert gitops.git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    with pytest.raises(MnemeError):
        gitops.git(repo, "definitely-not-a-command")


def test_repo_predicates(tmp_path):
    repo = make_repo(tmp_path / "r")
    assert gitops.is_git_repo(repo)
    assert not gitops.is_git_repo(tmp_path / "not-a-repo")
    assert gitops.is_clean(repo)
    (repo / "dirty.txt").write_text("x", encoding="utf-8")
    assert not gitops.is_clean(repo)
    assert gitops.current_branch(repo) == "main"
    assert not gitops.has_remote(repo)


def test_sync_and_branch_without_remote(tmp_path):
    repo = make_repo(tmp_path / "r")
    gitops.create_branch(repo, "mneme/harvest-test")
    assert gitops.current_branch(repo) == "mneme/harvest-test"
    gitops.sync_main(repo)
    assert gitops.current_branch(repo) == "main"


def test_sync_pulls_from_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    repo = make_repo(tmp_path / "r")
    gitops.git(repo, "remote", "add", "origin", str(remote))
    gitops.git(repo, "push", "-u", "origin", "main")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True, capture_output=True)
    (other / "new.txt").write_text("new\n", encoding="utf-8")
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(other),
         "add", "-A"], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(other),
         "commit", "-m", "upstream"], check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(other), "push"], check=True, capture_output=True)
    gitops.sync_main(repo)
    assert (repo / "new.txt").exists()


def test_restore_discards_uncommitted(tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "seed.txt").write_text("mutated\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("junk\n", encoding="utf-8")
    gitops.restore(repo)
    assert (repo / "seed.txt").read_text(encoding="utf-8") == "seed\n"
    assert not (repo / "untracked.txt").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_gitops_basic.py -v`
Expected: FAIL — `AttributeError: module 'mneme_core.paths' has no attribute 'submitted_path'`, then `ModuleNotFoundError` for `gitops`.

- [ ] **Step 3: Implement**

Append to `core/mneme_core/paths.py`:

```python
def submitted_path(home: Path) -> Path:
    return home / "submitted.jsonl"
```

Create `core/mneme_core/gitops.py`:

```python
"""Git side effects for harvest — subprocess-wrapped, never networked implicitly (spec §7.3, §8)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import MnemeError


def git(repo: Path, *args: str) -> str:
    cmd = [
        "git",
        "-c", "user.name=mneme",
        "-c", "user.email=mneme@localhost",
        "-C", str(repo),
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise MnemeError(f"git {' '.join(args)} failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def is_git_repo(repo: Path) -> bool:
    return (repo / ".git").exists()


def is_clean(repo: Path) -> bool:
    return git(repo, "status", "--porcelain") == ""


def current_branch(repo: Path) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def has_remote(repo: Path) -> bool:
    return "origin" in git(repo, "remote").splitlines()


def sync_main(repo: Path) -> None:
    git(repo, "checkout", "main")
    if has_remote(repo):
        git(repo, "pull", "--ff-only", "origin", "main")


def create_branch(repo: Path, name: str) -> None:
    git(repo, "checkout", "-b", name)


def restore(repo: Path) -> None:
    git(repo, "checkout", "--", ".")
    git(repo, "clean", "-fd")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_gitops_basic.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/paths.py core/mneme_core/gitops.py tests/core/test_gitops_basic.py
git commit -m "feat: git plumbing for harvest and submissions ledger path"
```

---

### Task 2: Commit with provenance + push (`gitops.py`, part 2)

**Files:**
- Modify: `core/mneme_core/gitops.py` (append)
- Create: `tests/core/test_gitops_commit.py`

**Interfaces:**
- Consumes: Task 1's module.
- Produces: `commit_harvest(repo: Path, unit_lines: list[str], sources: list[str]) -> str` — stages everything (`add -A`), commits with subject `knowledge: harvest <YYYY-MM-DD> (<n> units)`, body = one `- <unit line>` per unit, then one blank line and one `Mneme-Source: <source>` trailer per unique source (sorted); returns the commit sha; `MnemeError` when there is nothing to commit; `push_branch(repo: Path, branch: str) -> None` (`push -u origin <branch>`); `push_main(repo: Path) -> None` (`push origin main`). Push helpers raise `MnemeError` when no remote exists — callers check `has_remote` first.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_gitops_commit.py`:

```python
import subprocess

import pytest

from mneme_core import gitops
from mneme_core.errors import MnemeError


def make_repo(root):
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(root),
         "add", "-A"], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(root),
         "commit", "-m", "seed"], check=True, capture_output=True,
    )
    return root


def test_commit_harvest_message_shape(tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "skills").mkdir()
    (repo / "skills" / "x.md").write_text("unit\n", encoding="utf-8")
    sha = gitops.commit_harvest(
        repo,
        ["skills/deploy-widget (new skill)", "facts/staging-env#db-resets (new fact)"],
        ["demo@s1", "demo@s1"],
    )
    assert len(sha) == 40
    message = gitops.git(repo, "log", "-1", "--format=%B")
    assert message.splitlines()[0].startswith("knowledge: harvest ")
    assert "(2 units)" in message.splitlines()[0]
    assert "- skills/deploy-widget (new skill)" in message
    assert "Mneme-Source: demo@s1" in message
    assert message.count("Mneme-Source:") == 1  # deduplicated


def test_commit_harvest_nothing_to_commit(tmp_path):
    repo = make_repo(tmp_path / "r")
    with pytest.raises(MnemeError):
        gitops.commit_harvest(repo, ["x"], ["s"])


def test_push_branch_to_bare_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    repo = make_repo(tmp_path / "r")
    gitops.git(repo, "remote", "add", "origin", str(remote))
    gitops.git(repo, "push", "-u", "origin", "main")
    gitops.create_branch(repo, "mneme/harvest-x")
    (repo / "new.txt").write_text("n\n", encoding="utf-8")
    gitops.commit_harvest(repo, ["facts/t#k (new fact)"], ["s1"])
    gitops.push_branch(repo, "mneme/harvest-x")
    remote_branches = subprocess.run(
        ["git", "-C", str(remote), "branch"], capture_output=True, text=True, check=True
    ).stdout
    assert "mneme/harvest-x" in remote_branches


def test_push_without_remote_raises(tmp_path):
    repo = make_repo(tmp_path / "r")
    with pytest.raises(MnemeError):
        gitops.push_branch(repo, "main")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_gitops_commit.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'commit_harvest'`.

- [ ] **Step 3: Append to `core/mneme_core/gitops.py`**

```python
from datetime import datetime, timezone


def commit_harvest(repo: Path, unit_lines: list[str], sources: list[str]) -> str:
    git(repo, "add", "-A")
    if git(repo, "status", "--porcelain") == "":
        raise MnemeError("nothing to commit for this harvest")
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"knowledge: harvest {date} ({len(unit_lines)} units)"
    body_lines = [f"- {line}" for line in unit_lines]
    trailers = [f"Mneme-Source: {s}" for s in sorted(set(sources))]
    message = subject + "\n\n" + "\n".join(body_lines) + "\n\n" + "\n".join(trailers) + "\n"
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def push_branch(repo: Path, branch: str) -> None:
    if not has_remote(repo):
        raise MnemeError("no 'origin' remote to push to")
    git(repo, "push", "-u", "origin", branch)


def push_main(repo: Path) -> None:
    if not has_remote(repo):
        raise MnemeError("no 'origin' remote to push to")
    git(repo, "push", "origin", "main")
```

(Move the `datetime` import to the top of the file with the existing imports.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_gitops_commit.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/gitops.py tests/core/test_gitops_commit.py
git commit -m "feat: harvest commits with provenance trailers and push helpers"
```

---

### Task 3: PR creation via `gh` (`gitops.py`, part 3)

**Files:**
- Modify: `core/mneme_core/gitops.py` (append)
- Create: `tests/core/test_gitops_pr.py`

**Interfaces:**
- Consumes: Task 2's module, `shutil.which`.
- Produces: `open_pr(repo: Path, branch: str, title: str, body: str) -> str` — when `gh` is on PATH: runs `gh pr create --head <branch> --title <title> --body <body>` in the repo and returns its stdout (the PR URL); when `gh` is missing OR the command fails: returns a fallback instruction string beginning `manual:` that includes the branch name and title (never raises — a missing forge CLI must not lose the harvest, the branch is already pushed).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_gitops_pr.py`:

```python
import os
import stat
import subprocess

from mneme_core import gitops


def make_repo(root):
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(root),
         "add", "-A"], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(root),
         "commit", "-m", "seed"], check=True, capture_output=True,
    )
    return root


def shim_gh(tmp_path, monkeypatch, script):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(script, encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def test_open_pr_uses_gh(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    shim_gh(
        tmp_path, monkeypatch,
        "#!/bin/sh\necho https://github.com/acme/kb/pull/7\n",
    )
    url = gitops.open_pr(repo, "mneme/harvest-x", "knowledge: harvest", "body text")
    assert url == "https://github.com/acme/kb/pull/7"


def test_open_pr_gh_failure_degrades(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    shim_gh(tmp_path, monkeypatch, "#!/bin/sh\necho boom >&2\nexit 1\n")
    result = gitops.open_pr(repo, "mneme/harvest-x", "title", "body")
    assert result.startswith("manual:")
    assert "mneme/harvest-x" in result


def test_open_pr_gh_missing_degrades(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    bindir = tmp_path / "emptybin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir))
    result = gitops.open_pr(repo, "b", "t", "body")
    assert result.startswith("manual:")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_gitops_pr.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'open_pr'`.

- [ ] **Step 3: Append to `core/mneme_core/gitops.py`**

```python
import shutil


def open_pr(repo: Path, branch: str, title: str, body: str) -> str:
    fallback = (
        f"manual: branch '{branch}' is pushed — open the pull request yourself"
        f" (title: {title})"
    )
    if shutil.which("gh") is None:
        return fallback
    result = subprocess.run(
        ["gh", "pr", "create", "--head", branch, "--title", title, "--body", body],
        capture_output=True, text=True, cwd=str(repo),
    )
    if result.returncode != 0:
        return fallback
    return result.stdout.strip()
```

(Move the `shutil` import to the top of the file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_gitops_pr.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/gitops.py tests/core/test_gitops_pr.py
git commit -m "feat: PR creation via gh with graceful degradation"
```

---

### Task 4: Applying skill candidates (`harvest.py`, part 1)

**Files:**
- Create: `core/mneme_core/harvest.py`, `tests/core/test_harvest_skills.py`

**Interfaces:**
- Consumes: `staging.Candidate`, `units.parse_frontmatter`, `MnemeError`.
- Produces: `apply_skill(repo: Path, cand: Candidate) -> str` — for `edit="new"`: writes `cand.body` to `skills/<name>/SKILL.md` where `<name>` comes from the body's frontmatter `name` (MnemeError if that skill dir already exists); for `edit="update"`: `cand.target_unit` must be `skills/<name>` and that SKILL.md must exist (MnemeError otherwise), body replaces the file. Returns a unit line for the commit message: `skills/<name> (new skill)` / `skills/<name> (updated skill)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_harvest_skills.py`:

```python
import pytest

from mneme_core import compose, harvest
from mneme_core.errors import MnemeError
from mneme_core.staging import Candidate, candidate_id


def skill_body(name="deploy-widget", description="Use when deploying widgets"):
    return compose.render_skill_unit(
        name, description, "1. steps", "what failed first",
        source="demo@s1", captured="2026-08-11",
    )


def make_candidate(body, edit="new", target_unit=""):
    return Candidate(
        id=candidate_id("skill", "acme-knowledge", body),
        type="skill", edit=edit, target="acme-knowledge",
        body=body, target_unit=target_unit,
    )


def test_apply_new_skill(tmp_path):
    body = skill_body()
    line = harvest.apply_skill(tmp_path, make_candidate(body))
    written = tmp_path / "skills" / "deploy-widget" / "SKILL.md"
    assert written.read_text(encoding="utf-8") == body
    assert line == "skills/deploy-widget (new skill)"


def test_apply_new_skill_conflict(tmp_path):
    body = skill_body()
    harvest.apply_skill(tmp_path, make_candidate(body))
    with pytest.raises(MnemeError):
        harvest.apply_skill(tmp_path, make_candidate(body))


def test_apply_update_replaces(tmp_path):
    harvest.apply_skill(tmp_path, make_candidate(skill_body()))
    new_body = skill_body(description="Use when deploying widgets after the LB fix")
    line = harvest.apply_skill(
        tmp_path,
        make_candidate(new_body, edit="update", target_unit="skills/deploy-widget"),
    )
    assert line == "skills/deploy-widget (updated skill)"
    text = (tmp_path / "skills" / "deploy-widget" / "SKILL.md").read_text(encoding="utf-8")
    assert "after the LB fix" in text


def test_apply_update_missing_target(tmp_path):
    with pytest.raises(MnemeError):
        harvest.apply_skill(
            tmp_path,
            make_candidate(skill_body(), edit="update", target_unit="skills/deploy-widget"),
        )


def test_apply_update_name_mismatch(tmp_path):
    harvest.apply_skill(tmp_path, make_candidate(skill_body()))
    other = skill_body(name="other-skill")
    with pytest.raises(MnemeError):
        harvest.apply_skill(
            tmp_path, make_candidate(other, edit="update", target_unit="skills/deploy-widget")
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_harvest_skills.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.harvest'`.

- [ ] **Step 3: Implement `core/mneme_core/harvest.py`**

```python
"""Applying approved candidates to knowledge-repo clones (spec §7.3)."""
from __future__ import annotations

from pathlib import Path

from . import units
from .errors import MnemeError
from .staging import Candidate


def _skill_name(cand: Candidate) -> str:
    meta, _body = units.parse_frontmatter(cand.body)
    name = str(meta.get("name", ""))
    if not name:
        raise MnemeError(f"candidate {cand.id}: skill body has no frontmatter name")
    return name


def apply_skill(repo: Path, cand: Candidate) -> str:
    name = _skill_name(cand)
    skill_md = repo / "skills" / name / "SKILL.md"
    if cand.edit == "new":
        if skill_md.exists():
            raise MnemeError(
                f"candidate {cand.id}: skills/{name} already exists — expected an update edit"
            )
        skill_md.parent.mkdir(parents=True, exist_ok=True)
        skill_md.write_text(cand.body, encoding="utf-8")
        return f"skills/{name} (new skill)"
    expected = cand.target_unit.removeprefix("skills/")
    if name != expected:
        raise MnemeError(
            f"candidate {cand.id}: body names skill {name!r} but targets {cand.target_unit!r}"
        )
    if not skill_md.exists():
        raise MnemeError(f"candidate {cand.id}: update target {cand.target_unit} not found")
    skill_md.write_text(cand.body, encoding="utf-8")
    return f"skills/{name} (updated skill)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_harvest_skills.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/harvest.py tests/core/test_harvest_skills.py
git commit -m "feat: apply skill candidates to knowledge repos"
```

---

### Task 5: Applying fact candidates (`harvest.py`, part 2)

**Files:**
- Modify: `core/mneme_core/harvest.py` (append)
- Create: `tests/core/test_harvest_facts.py`

**Interfaces:**
- Consumes: `units.parse_frontmatter`, `units.parse_bullet_line`, `units.normalize_topic_key`, `units.serialize_frontmatter`.
- Produces: `apply_fact(repo: Path, cand: Candidate) -> str` — the bullet line is `cand.body` stripped to one line; target file is `facts/<cand.topic>.md` (MnemeError when `cand.topic` is empty). `edit="new"`: create the file with `---\ntopic: <topic>\n---\n` frontmatter when missing, then append the bullet as the last line (exactly one bullet added; existing content untouched); duplicate topic-key in that file → MnemeError (should have been an update). `edit="update"`: `target_unit` is `facts/<stem>#<key>`; find the single bullet line whose topic-key == `<key>` in `facts/<stem>.md` and replace exactly that line (MnemeError when the file or the key is missing). Returns `facts/<topic>#<key> (new fact)` / `(updated fact)` unit lines.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_harvest_facts.py`:

```python
import pytest

from mneme_core import compose, harvest, units
from mneme_core.errors import MnemeError
from mneme_core.staging import Candidate, candidate_id


def bullet(text="Staging DB resets nightly at 04:00 UTC", category="constraint"):
    return compose.render_fact_bullet(category, text, ["staging"], verified="2026-08-11")


def make_candidate(body, topic="staging-env", edit="new", target_unit=""):
    return Candidate(
        id=candidate_id("fact", "acme-knowledge", body),
        type="fact", edit=edit, target="acme-knowledge",
        body=body, topic=topic, target_unit=target_unit,
    )


def test_apply_new_fact_creates_file(tmp_path):
    line = harvest.apply_fact(tmp_path, make_candidate(bullet()))
    text = (tmp_path / "facts" / "staging-env.md").read_text(encoding="utf-8")
    meta, body = units.parse_frontmatter(text)
    assert meta["topic"] == "staging-env"
    assert body.strip().startswith("- [constraint] Staging DB resets nightly")
    assert line == "facts/staging-env#staging-db-resets-nightly-at-04 (new fact)"


def test_apply_new_fact_appends_preserving_existing(tmp_path):
    harvest.apply_fact(tmp_path, make_candidate(bullet()))
    other = bullet(text="v2 API truncates batch writes over 500 items", category="gotcha")
    harvest.apply_fact(tmp_path, make_candidate(other))
    text = (tmp_path / "facts" / "staging-env.md").read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if l.startswith("- [")]
    assert len(lines) == 2
    assert "resets nightly" in lines[0]
    assert "truncates batch" in lines[1]


def test_apply_new_duplicate_topic_key_rejected(tmp_path):
    harvest.apply_fact(tmp_path, make_candidate(bullet()))
    same_key = bullet(text="Staging DB resets nightly at 04:00 UTC exactly")
    assert units.normalize_topic_key(
        "Staging DB resets nightly at 04:00 UTC exactly"
    ) == units.normalize_topic_key("Staging DB resets nightly at 04:00 UTC")
    with pytest.raises(MnemeError):
        harvest.apply_fact(tmp_path, make_candidate(same_key))


def test_apply_update_replaces_single_line(tmp_path):
    harvest.apply_fact(tmp_path, make_candidate(bullet()))
    harvest.apply_fact(
        tmp_path,
        make_candidate(
            bullet(text="v2 API truncates batch writes over 500 items", category="gotcha"),
        ),
    )
    new_line = compose.render_fact_bullet(
        "constraint", "Staging DB resets nightly at 03:00 UTC now", ["staging"],
        verified="2026-08-12",
    )
    result = harvest.apply_fact(
        tmp_path,
        make_candidate(
            new_line, edit="update",
            target_unit="facts/staging-env#staging-db-resets-nightly-at-04",
        ),
    )
    assert result.endswith("(updated fact)")
    text = (tmp_path / "facts" / "staging-env.md").read_text(encoding="utf-8")
    assert "03:00 UTC now" in text
    assert "04:00 UTC" not in text
    assert "truncates batch" in text  # untouched neighbor


def test_apply_update_missing_key_or_file(tmp_path):
    with pytest.raises(MnemeError):
        harvest.apply_fact(
            tmp_path,
            make_candidate(bullet(), edit="update", target_unit="facts/absent#nope"),
        )
    harvest.apply_fact(tmp_path, make_candidate(bullet()))
    with pytest.raises(MnemeError):
        harvest.apply_fact(
            tmp_path,
            make_candidate(bullet(), edit="update", target_unit="facts/staging-env#no-such-key"),
        )


def test_apply_fact_requires_topic(tmp_path):
    with pytest.raises(MnemeError):
        harvest.apply_fact(tmp_path, make_candidate(bullet(), topic=""))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_harvest_facts.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'apply_fact'`.

- [ ] **Step 3: Append to `core/mneme_core/harvest.py`**

```python
def apply_fact(repo: Path, cand: Candidate) -> str:
    if cand.edit == "new" and not cand.topic:
        raise MnemeError(f"candidate {cand.id}: fact candidate has no topic")
    line = cand.body.strip()
    bullet = units.parse_bullet_line(line, 1)

    if cand.edit == "new":
        path = repo / "facts" / f"{cand.topic}.md"
        if path.exists():
            meta, body = units.parse_frontmatter(path.read_text(encoding="utf-8-sig"))
        else:
            meta, body = {"topic": cand.topic}, ""
        for n, existing in enumerate(body.splitlines(), start=1):
            if existing.startswith("- ["):
                if units.parse_bullet_line(existing, n).topic_key == bullet.topic_key:
                    raise MnemeError(
                        f"candidate {cand.id}: topic key '{bullet.topic_key}' already exists"
                        f" in facts/{cand.topic}.md — expected an update edit"
                    )
        new_body = body.rstrip("\n")
        new_body = (new_body + "\n" if new_body else "") + line + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(units.serialize_frontmatter(meta, new_body), encoding="utf-8")
        return f"facts/{cand.topic}#{bullet.topic_key} (new fact)"

    if "#" not in cand.target_unit or not cand.target_unit.startswith("facts/"):
        raise MnemeError(f"candidate {cand.id}: malformed fact target_unit {cand.target_unit!r}")
    file_part, key = cand.target_unit.removeprefix("facts/").split("#", 1)
    path = repo / "facts" / f"{file_part}.md"
    if not path.exists():
        raise MnemeError(f"candidate {cand.id}: update target file {path.name} not found")
    meta, body = units.parse_frontmatter(path.read_text(encoding="utf-8-sig"))
    out_lines: list[str] = []
    replaced = False
    for n, existing in enumerate(body.splitlines(), start=1):
        if not replaced and existing.startswith("- ["):
            try:
                if units.parse_bullet_line(existing, n).topic_key == key:
                    out_lines.append(line)
                    replaced = True
                    continue
            except MnemeError:
                pass
        out_lines.append(existing)
    if not replaced:
        raise MnemeError(
            f"candidate {cand.id}: no bullet with topic key '{key}' in facts/{file_part}.md"
        )
    path.write_text(
        units.serialize_frontmatter(meta, "\n".join(out_lines) + "\n"), encoding="utf-8"
    )
    return f"facts/{file_part}#{key} (updated fact)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_harvest_facts.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/harvest.py tests/core/test_harvest_facts.py
git commit -m "feat: apply fact candidates as line-granular delta edits"
```

---

### Task 6: Batch apply with validation gate (`harvest.py`, part 3)

**Files:**
- Modify: `core/mneme_core/harvest.py` (append)
- Create: `tests/core/test_harvest_batch.py`

**Interfaces:**
- Consumes: `gitops` (is_git_repo, is_clean, sync_main, create_branch, commit_harvest, push_branch, push_main, open_pr, restore, has_remote), `lint.lint_repo`, `lint.has_errors`, `scan.scan_text`, `scan.has_blockers`, `scaffold.regenerate_index_skill`, `registry.get_plugin`, `staging.remove_candidate`, `paths.submitted_path`.
- Produces: `@dataclass HarvestResult(target: str, units: list[str], branch: str, commit: str, pr: str, mode: str)`; `apply_batch(home: Path, target_name: str, candidates: list[Candidate], *, push: bool = True) -> HarvestResult` — pipeline:
  1. Resolve the registered plugin (MnemeError if unknown); repo = its `path`; preconditions: `is_git_repo`, `is_clean` (MnemeError naming the problem).
  2. Quarantined candidates are refused outright (MnemeError — redact first).
  3. `sync_main`; for mode `pr`: `create_branch(repo, "mneme/harvest-<YYYYMMDD-HHMMSS>")`; for mode `commit`: stay on main.
  4. Apply every candidate (`apply_skill`/`apply_fact`); then regenerate the knowledge-index skill when `.claude-plugin/plugin.json` exists (name/description read from it); then the gate: `lint_repo` error-free and every candidate body re-scans blocker-free.
  5. Any failure in 4 → `gitops.restore(repo)`, checkout main, raise `MnemeError` (staging untouched — nothing lost).
  6. `commit_harvest`; when `push` and a remote exists: mode `pr` → `push_branch` + `open_pr` (title = commit subject, body = unit list); mode `commit` → `push_main`. No remote → skip network, `pr` field explains.
  7. Append one record to `submitted.jsonl` (`{"target", "branch", "commit", "mode", "pr", "units", "candidates", "ts"}`); remove each candidate from staging.
  8. For mode `pr`: checkout back to main, leaving the branch for iteration.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_harvest_batch.py`:

```python
import json
import subprocess

import pytest

from mneme_core import compose, gitops, harvest, paths, registry, scaffold, staging
from mneme_core.errors import MnemeError
from mneme_core.staging import Candidate, candidate_id


def stage_skill(home, target="acme-knowledge", name="deploy-widget"):
    body = compose.render_skill_unit(
        name, "Use when deploying the widget service", "1. steps", "what failed",
        source="demo@s1", captured="2026-08-11",
    )
    cand = Candidate(
        id=candidate_id("skill", target, body), type="skill", edit="new",
        target=target, body=body, provenance={"source": "demo@s1", "captured": "2026-08-11"},
    )
    staging.write_candidate(home, cand)
    return cand


def stage_fact(home, target="acme-knowledge"):
    body = compose.render_fact_bullet(
        "constraint", "Staging DB resets nightly at 04:00 UTC", ["staging"],
        verified="2026-08-11",
    )
    cand = Candidate(
        id=candidate_id("fact", target, body), type="fact", edit="new",
        target=target, body=body, topic="staging-env",
        provenance={"source": "demo@s1", "captured": "2026-08-11"},
    )
    staging.write_candidate(home, cand)
    return cand


def test_apply_batch_pr_mode_with_remote(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    gitops.git(target, "remote", "add", "origin", str(remote))
    gitops.git(target, "push", "-u", "origin", "main")
    skill = stage_skill(home)
    fact = stage_fact(home)

    result = harvest.apply_batch(home, "acme-knowledge", [skill, fact])
    assert result.mode == "pr"
    assert result.branch.startswith("mneme/harvest-")
    assert len(result.units) == 2
    assert result.pr.startswith("manual:")  # no real gh in test PATH... shim not installed
    # branch pushed to the bare remote
    remote_branches = subprocess.run(
        ["git", "-C", str(remote), "branch"], capture_output=True, text=True, check=True
    ).stdout
    assert result.branch in remote_branches
    # repo back on main, clean; branch carries the harvest commit
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    log = gitops.git(target, "log", result.branch, "-1", "--format=%B")
    assert "knowledge: harvest" in log
    assert "Mneme-Source: demo@s1" in log
    # staging emptied, ledger written
    assert staging.load_candidates(home) == []
    ledger = [json.loads(l) for l in paths.submitted_path(home).read_text(encoding="utf-8").splitlines()]
    assert ledger[0]["target"] == "acme-knowledge"
    assert len(ledger[0]["units"]) == 2


def test_apply_batch_commit_mode_no_remote(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "personal-kb", owner="demo", mode="commit")
    skill = stage_skill(home, target="personal-kb")
    result = harvest.apply_batch(home, "personal-kb", [skill])
    assert result.mode == "commit"
    assert result.branch == "main"
    assert "no remote" in result.pr
    log = gitops.git(target, "log", "-1", "--format=%s")
    assert log.startswith("knowledge: harvest")
    assert (target / "skills" / "deploy-widget" / "SKILL.md").exists()


def test_apply_batch_refuses_quarantined(tmp_path):
    home = tmp_path / "home"
    scaffold.create(home, "acme-knowledge", owner="demo")
    cand = stage_skill(home)
    staging.quarantine(home, cand.id)
    quarantined = staging.load_candidates(home, include_quarantined=True)[0]
    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "acme-knowledge", [quarantined])


def test_apply_batch_dirty_repo_refused(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    (target / "junk.txt").write_text("dirty", encoding="utf-8")
    cand = stage_skill(home)
    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "acme-knowledge", [cand])


def test_apply_batch_failure_restores_repo_and_keeps_staging(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    good = stage_skill(home)
    # second candidate collides with the first (same skill, edit=new) -> apply fails mid-batch
    dup_body = good.body.replace("what failed", "what failed differently")
    dup = Candidate(
        id=candidate_id("skill", "acme-knowledge", dup_body), type="skill", edit="new",
        target="acme-knowledge", body=dup_body,
        provenance={"source": "demo@s1", "captured": "2026-08-11"},
    )
    staging.write_candidate(home, dup)
    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "acme-knowledge", [good, dup])
    assert gitops.is_clean(target)
    assert gitops.current_branch(target) == "main"
    assert not (target / "skills" / "deploy-widget").exists()
    assert len(staging.load_candidates(home)) == 2  # nothing lost


def test_apply_batch_unknown_target(tmp_path):
    home = tmp_path / "home"
    cand = stage_skill(home, target="ghost-kb")
    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "ghost-kb", [cand])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_harvest_batch.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'apply_batch'`.

- [ ] **Step 3: Append to `core/mneme_core/harvest.py`**

```python
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import gitops, lint, paths, registry, scan
from . import scaffold as scaffold_mod


@dataclass
class HarvestResult:
    target: str
    units: list[str] = field(default_factory=list)
    branch: str = ""
    commit: str = ""
    pr: str = ""
    mode: str = ""


def _regenerate_index(repo: Path) -> None:
    manifest = repo / ".claude-plugin" / "plugin.json"
    if not manifest.exists():
        return
    data = json.loads(manifest.read_text(encoding="utf-8"))
    (repo / "skills" / "knowledge-index").mkdir(parents=True, exist_ok=True)
    scaffold_mod.regenerate_index_skill(
        repo, str(data.get("name", repo.name)), str(data.get("description", ""))
    )


def apply_batch(
    home: Path, target_name: str, candidates: list[Candidate], *, push: bool = True
) -> HarvestResult:
    plugin = registry.get_plugin(home, target_name)
    if plugin is None:
        raise MnemeError(f"unknown harvest target: {target_name}")
    repo = Path(plugin.path)
    if not gitops.is_git_repo(repo):
        raise MnemeError(f"{repo} is not a git repository")
    if not gitops.is_clean(repo):
        raise MnemeError(f"{repo} has uncommitted changes — commit or stash them first")
    for cand in candidates:
        if cand.status == "quarantined":
            raise MnemeError(
                f"candidate {cand.id} is quarantined — redact and re-stage before harvesting"
            )

    gitops.sync_main(repo)
    result = HarvestResult(target=target_name, mode=plugin.mode)
    if plugin.mode == "pr":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        result.branch = f"mneme/harvest-{stamp}"
        gitops.create_branch(repo, result.branch)
    else:
        result.branch = "main"

    try:
        for cand in candidates:
            if cand.type == "skill":
                result.units.append(apply_skill(repo, cand))
            else:
                result.units.append(apply_fact(repo, cand))
        _regenerate_index(repo)
        issues = lint.lint_repo(repo)
        if lint.has_errors(issues):
            details = "; ".join(
                f"{i.code} {i.message}" for i in issues if i.severity == "error"
            )
            raise MnemeError(f"harvest fails repo lint: {details}")
        for cand in candidates:
            if scan.has_blockers(scan.scan_text(cand.body)):
                raise MnemeError(f"candidate {cand.id} re-scan found blocking findings")
    except MnemeError:
        gitops.restore(repo)
        if plugin.mode == "pr":
            gitops.git(repo, "checkout", "main")
            gitops.git(repo, "branch", "-D", result.branch)
        raise

    sources = [str(c.provenance.get("source", "unknown")) for c in candidates]
    result.commit = gitops.commit_harvest(repo, result.units, sources)

    if plugin.mode == "pr":
        if push and gitops.has_remote(repo):
            gitops.push_branch(repo, result.branch)
            title = f"knowledge: harvest ({len(result.units)} units)"
            result.pr = gitops.open_pr(repo, result.branch, title, "\n".join(result.units))
        else:
            result.pr = "no remote — branch is local only"
        gitops.git(repo, "checkout", "main")
    else:
        if push and gitops.has_remote(repo):
            gitops.push_main(repo)
            result.pr = "pushed to main"
        else:
            result.pr = "no remote — committed to local main"

    record = {
        "target": target_name,
        "branch": result.branch,
        "commit": result.commit,
        "mode": plugin.mode,
        "pr": result.pr,
        "units": result.units,
        "candidates": [c.id for c in candidates],
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    paths.ensure_layout(home)
    with paths.submitted_path(home).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    from . import staging as staging_mod

    for cand in candidates:
        staging_mod.remove_candidate(home, cand.id)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_harvest_batch.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/harvest.py tests/core/test_harvest_batch.py
git commit -m "feat: batch harvest with validation gate, provenance, and ledger"
```

---

### Task 7: `mneme share list` + `mneme share diff`

**Files:**
- Modify: `core/mneme_core/cli.py`
- Create: `tests/core/test_cli_share_view.py`

**Interfaces:**
- Consumes: `staging.load_candidates`, `registry.get_plugin`, `harvest._skill_name`, `units`, `difflib`.
- Produces: `mneme share list [--all]` — candidates grouped by target: a `<target>:` header line then `  <id>  <type>/<edit>  conf=<confidence>` per candidate, with ` [QUARANTINED]`, ` [boundary]`, ` [similar: <unit id>]` suffixes when applicable (quarantined shown only with `--all`); empty staging prints `nothing staged`. `mneme share diff ID` — for `edit=new`: prints the candidate body; for `edit=update`: unified diff (`difflib.unified_diff`) between the current unit content in the target's clone (skill file content, or the single existing bullet line) and the candidate body; unknown id → MnemeError.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_cli_share_view.py` (uses the shared `seed` helper from the File Structure section, verbatim):

```python
import json

from mneme_core import scaffold, staging
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def seed(tmp_path, capsys, name="acme-knowledge", mode="pr"):
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo", mode=mode)
    props = {
        "proposals": [
            {
                "type": "skill", "edit": "new", "target": name,
                "name": "deploy-widget", "description": "Use when deploying the widget service",
                "procedure": "1. preflight\n2. cutover",
                "failure_pattern": "restart loop hits the LB cache",
                "confidence": 0.9, "rationale": "verified",
            },
            {
                "type": "fact", "edit": "new", "target": name, "topic": "staging-env",
                "category": "constraint", "text": "Staging DB resets nightly at 04:00 UTC",
                "tags": ["staging"], "confidence": 0.8, "rationale": "observed",
            },
        ]
    }
    p = tmp_path / "props.json"
    p.write_text(json.dumps(props), encoding="utf-8")
    run(capsys, "--home", str(home), "distill", "ingest", str(p), "--source", "demo@s1")
    return home, target


def test_share_list_groups_by_target(tmp_path, capsys):
    home, _ = seed(tmp_path, capsys)
    code, out, _ = run(capsys, "--home", str(home), "share", "list")
    assert code == 0
    assert "acme-knowledge:" in out
    assert "skill/new" in out and "fact/new" in out
    assert "conf=0.9" in out


def test_share_list_hides_quarantined_without_all(tmp_path, capsys):
    home, _ = seed(tmp_path, capsys)
    cand = staging.load_candidates(home)[0]
    staging.quarantine(home, cand.id)
    code, out, _ = run(capsys, "--home", str(home), "share", "list")
    assert cand.id not in out
    code, out, _ = run(capsys, "--home", str(home), "share", "list", "--all")
    assert cand.id in out and "[QUARANTINED]" in out


def test_share_list_empty(tmp_path, capsys):
    code, out, _ = run(capsys, "--home", str(tmp_path / "h"), "share", "list")
    assert code == 0
    assert "nothing staged" in out


def test_share_diff_new_prints_body(tmp_path, capsys):
    home, _ = seed(tmp_path, capsys)
    skill = next(c for c in staging.load_candidates(home) if c.type == "skill")
    code, out, _ = run(capsys, "--home", str(home), "share", "diff", skill.id)
    assert code == 0
    assert "## Failure pattern" in out


def test_share_diff_update_shows_unified_diff(tmp_path, capsys):
    home, target = seed(tmp_path, capsys)
    skill = next(c for c in staging.load_candidates(home) if c.type == "skill")
    from mneme_core import harvest

    harvest.apply_batch(home, "acme-knowledge", [skill], push=False)
    props = {
        "proposals": [
            {
                "type": "skill", "edit": "update", "target": "acme-knowledge",
                "target_unit": "skills/deploy-widget",
                "name": "deploy-widget", "description": "Use when deploying the widget service",
                "procedure": "1. preflight\n2. cutover\n3. verify",
                "failure_pattern": "restart loop hits the LB cache",
                "confidence": 0.9, "rationale": "improved",
            }
        ]
    }
    p = tmp_path / "props2.json"
    p.write_text(json.dumps(props), encoding="utf-8")
    run(capsys, "--home", str(home), "distill", "ingest", str(p))
    update = staging.load_candidates(home)[0]
    code, out, _ = run(capsys, "--home", str(home), "share", "diff", update.id)
    assert code == 0
    assert "+3. verify" in out.replace(" ", "")


def test_share_diff_unknown_id(tmp_path, capsys):
    code, _, err = run(capsys, "--home", str(tmp_path / "h"), "share", "diff", "nope")
    assert code == 1
    assert "mneme:" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_cli_share_view.py -v`
Expected: FAIL — argparse `invalid choice: 'share'`.

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

Parser additions:

```python
    p_share = sub.add_parser("share")
    share_sub = p_share.add_subparsers(dest="share_command", required=True)
    p_slist2 = share_sub.add_parser("list")
    p_slist2.add_argument("--all", action="store_true")
    p_sdiff = share_sub.add_parser("diff")
    p_sdiff.add_argument("id")
```

Dispatch: `if args.command == "share": return _share_cmd(home, args)`.

Handler:

```python
def _share_cmd(home: Path, args: argparse.Namespace) -> int:
    from . import staging as staging_mod

    if args.share_command == "list":
        cands = staging_mod.load_candidates(home, include_quarantined=args.all)
        if not cands:
            print("nothing staged")
            return 0
        by_target: dict[str, list] = {}
        for c in cands:
            by_target.setdefault(c.target, []).append(c)
        for target in sorted(by_target):
            print(f"{target}:")
            for c in by_target[target]:
                suffix = ""
                if c.status == "quarantined":
                    suffix += " [QUARANTINED]"
                if c.boundary_warning:
                    suffix += " [boundary]"
                if c.similar_to:
                    suffix += f" [similar: {c.similar_to}]"
                print(f"  {c.id}  {c.type}/{c.edit}  conf={c.confidence}{suffix}")
        return 0

    if args.share_command == "diff":
        return _share_diff(home, args)
    return 1


def _share_diff(home: Path, args: argparse.Namespace) -> int:
    import difflib

    from . import harvest as harvest_mod
    from . import registry as registry_mod
    from . import staging as staging_mod
    from . import units as units_mod

    cands = {c.id: c for c in staging_mod.load_candidates(home, include_quarantined=True)}
    cand = cands.get(args.id)
    if cand is None:
        raise MnemeError(f"no staged candidate with id: {args.id}")
    if cand.edit == "new":
        print(cand.body)
        return 0
    plugin = registry_mod.get_plugin(home, cand.target)
    if plugin is None:
        raise MnemeError(f"candidate targets unknown plugin: {cand.target}")
    repo = Path(plugin.path)
    if cand.type == "skill":
        name = cand.target_unit.removeprefix("skills/")
        existing_path = repo / "skills" / name / "SKILL.md"
        if not existing_path.exists():
            raise MnemeError(f"update target {cand.target_unit} not found in {repo}")
        existing = existing_path.read_text(encoding="utf-8")
    else:
        file_part, key = cand.target_unit.removeprefix("facts/").split("#", 1)
        path = repo / "facts" / f"{file_part}.md"
        if not path.exists():
            raise MnemeError(f"update target file {path} not found")
        _meta, body = units_mod.parse_frontmatter(path.read_text(encoding="utf-8-sig"))
        existing = ""
        for n, line in enumerate(body.splitlines(), start=1):
            if line.startswith("- ["):
                try:
                    if units_mod.parse_bullet_line(line, n).topic_key == key:
                        existing = line + "\n"
                        break
                except MnemeError:
                    continue
        if not existing:
            raise MnemeError(f"no bullet with topic key '{key}' in {path.name}")
    new = cand.body if cand.body.endswith("\n") else cand.body + "\n"
    for line in difflib.unified_diff(
        existing.splitlines(), new.splitlines(),
        fromfile=f"current/{cand.target_unit}", tofile=f"candidate/{cand.id}", lineterm="",
    ):
        print(line)
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_cli_share_view.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_cli_share_view.py
git commit -m "feat: mneme share list and diff"
```

---

### Task 8: `mneme share apply` + `mneme decline`

**Files:**
- Modify: `core/mneme_core/cli.py`
- Create: `tests/core/test_cli_share_apply.py`

**Interfaces:**
- Consumes: `harvest.apply_batch`, `staging` (load, decline).
- Produces: `mneme share apply --ids ID[,ID...] [--no-push] [--dry-run]` — resolves the listed staged candidates (MnemeError on unknown/quarantined ids), groups by target, and per target either prints planned actions (`--dry-run`: `would apply <id> -> <target> (<type>/<edit>)` lines, zero side effects) or calls `apply_batch(..., push=not no_push)` and prints `harvested <target>: <n> units on <branch>` + `pr: <pr>` lines. `mneme decline ID --reason TEXT` — wraps `staging.decline` (loading the candidate first; MnemeError when missing) and prints `declined <id>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_cli_share_apply.py` (reuses the verbatim `seed` helper from Task 7's test file):

```python
import json

from mneme_core import paths, scaffold, staging
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def seed(tmp_path, capsys, name="acme-knowledge", mode="pr"):
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo", mode=mode)
    props = {
        "proposals": [
            {
                "type": "skill", "edit": "new", "target": name,
                "name": "deploy-widget", "description": "Use when deploying the widget service",
                "procedure": "1. preflight\n2. cutover",
                "failure_pattern": "restart loop hits the LB cache",
                "confidence": 0.9, "rationale": "verified",
            },
            {
                "type": "fact", "edit": "new", "target": name, "topic": "staging-env",
                "category": "constraint", "text": "Staging DB resets nightly at 04:00 UTC",
                "tags": ["staging"], "confidence": 0.8, "rationale": "observed",
            },
        ]
    }
    p = tmp_path / "props.json"
    p.write_text(json.dumps(props), encoding="utf-8")
    run(capsys, "--home", str(home), "distill", "ingest", str(p), "--source", "demo@s1")
    return home, target


def test_apply_dry_run_touches_nothing(tmp_path, capsys):
    home, target = seed(tmp_path, capsys)
    ids = ",".join(c.id for c in staging.load_candidates(home))
    code, out, _ = run(
        capsys, "--home", str(home), "share", "apply", "--ids", ids, "--dry-run"
    )
    assert code == 0
    assert out.count("would apply") == 2
    assert len(staging.load_candidates(home)) == 2
    assert not (target / "skills" / "deploy-widget").exists()


def test_apply_commit_mode_end_to_end(tmp_path, capsys):
    home, target = seed(tmp_path, capsys, name="personal-kb", mode="commit")
    ids = ",".join(c.id for c in staging.load_candidates(home))
    code, out, _ = run(capsys, "--home", str(home), "share", "apply", "--ids", ids)
    assert code == 0
    assert "harvested personal-kb: 2 units on main" in out
    assert (target / "skills" / "deploy-widget" / "SKILL.md").exists()
    assert staging.load_candidates(home) == []
    assert paths.submitted_path(home).exists()


def test_apply_unknown_id(tmp_path, capsys):
    home, _ = seed(tmp_path, capsys)
    code, _, err = run(capsys, "--home", str(home), "share", "apply", "--ids", "nope")
    assert code == 1
    assert "mneme:" in err


def test_decline_records_and_removes(tmp_path, capsys):
    home, _ = seed(tmp_path, capsys)
    cand = staging.load_candidates(home)[0]
    code, out, _ = run(
        capsys, "--home", str(home), "decline", cand.id, "--reason", "not durable"
    )
    assert code == 0
    assert f"declined {cand.id}" in out
    assert staging.is_declined(home, cand.body)
    assert len(staging.load_candidates(home)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_cli_share_apply.py -v`
Expected: FAIL — argparse `invalid choice: 'apply'` / `'decline'`.

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

Parser additions (inside the `share` group, plus a top-level `decline`):

```python
    p_sapply = share_sub.add_parser("apply")
    p_sapply.add_argument("--ids", required=True)
    p_sapply.add_argument("--no-push", action="store_true")
    p_sapply.add_argument("--dry-run", action="store_true")

    p_decline = sub.add_parser("decline")
    p_decline.add_argument("id")
    p_decline.add_argument("--reason", required=True)
```

Extend `_share_cmd` and the top-level dispatch:

```python
    if args.share_command == "apply":
        return _share_apply(home, args)
```

```python
        if args.command == "decline":
            from . import staging as staging_mod

            cands = {
                c.id: c
                for c in staging_mod.load_candidates(home, include_quarantined=True)
            }
            cand = cands.get(args.id)
            if cand is None:
                raise MnemeError(f"no staged candidate with id: {args.id}")
            staging_mod.decline(home, cand, args.reason)
            print(f"declined {args.id}")
            return 0
```

New handler:

```python
def _share_apply(home: Path, args: argparse.Namespace) -> int:
    from . import harvest as harvest_mod
    from . import staging as staging_mod

    wanted = [i.strip() for i in args.ids.split(",") if i.strip()]
    all_cands = {c.id: c for c in staging_mod.load_candidates(home)}
    missing = [i for i in wanted if i not in all_cands]
    if missing:
        raise MnemeError(f"unknown or quarantined candidate ids: {', '.join(missing)}")
    selected = [all_cands[i] for i in wanted]
    by_target: dict[str, list] = {}
    for c in selected:
        by_target.setdefault(c.target, []).append(c)

    if args.dry_run:
        for target in sorted(by_target):
            for c in by_target[target]:
                print(f"would apply {c.id} -> {target} ({c.type}/{c.edit})")
        return 0

    for target in sorted(by_target):
        result = harvest_mod.apply_batch(
            home, target, by_target[target], push=not args.no_push
        )
        print(f"harvested {target}: {len(result.units)} units on {result.branch}")
        print(f"pr: {result.pr}")
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_cli_share_apply.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_cli_share_apply.py
git commit -m "feat: mneme share apply and decline"
```

---

### Task 9: `mneme verify` — staleness sweep

**Files:**
- Modify: `core/mneme_core/cli.py`
- Create: `tests/core/test_cli_verify.py`

**Interfaces:**
- Consumes: `registry.get_plugin`, `units` (parse_frontmatter, parse_bullet_line, fact_unit_id, skill_unit_id).
- Produces: `mneme verify PLUGIN [--days N]` (default 90) — walks the registered plugin's clone: skill units read `metadata.mneme-last-verified`; fact bullets read their `(verified: …)` date; a unit is STALE when the date is missing or older than N days (against today UTC). Prints one line per stale unit: `<unit id>  last-verified=<date|none>  age-days=<n|unknown>`, then `stale <n> of <total> units`. Exit 2 when any stale unit exists, 0 when none (findings contract). Unknown plugin → MnemeError.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_cli_verify.py`:

```python
from datetime import datetime, timedelta, timezone

from mneme_core import registry, scaffold
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def old_date(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def make_kb(tmp_path, home):
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    d = target / "skills" / "old-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: old-skill\ndescription: d\nmetadata:\n"
        f"  mneme-last-verified: {old_date(200)}\n---\nBody\n",
        encoding="utf-8",
    )
    (target / "facts" / "mixed.md").write_text(
        "---\ntopic: mixed\n---\n"
        f"- [gotcha] Fresh fact #x (verified: {old_date(5)})\n"
        f"- [gotcha] Stale fact number two #x (verified: {old_date(120)})\n"
        "- [gotcha] Dateless fact number three #x\n",
        encoding="utf-8",
    )
    import subprocess

    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(target),
         "add", "-A"], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(target),
         "commit", "-m", "fixtures"], check=True, capture_output=True,
    )
    return target


def test_verify_reports_stale_units(tmp_path, capsys):
    home = tmp_path / "home"
    make_kb(tmp_path, home)
    code, out, _ = run(capsys, "--home", str(home), "verify", "acme-knowledge")
    assert code == 2
    assert "skills/old-skill" in out
    assert "last-verified=none" in out          # the dateless fact
    assert "stale 3 of" in out                   # old-skill + stale fact + dateless fact
    assert "Fresh fact" not in out


def test_verify_days_override(tmp_path, capsys):
    home = tmp_path / "home"
    make_kb(tmp_path, home)
    code, out, _ = run(capsys, "--home", str(home), "verify", "acme-knowledge", "--days", "365")
    assert code == 2                              # dateless fact is always stale
    assert "stale 1 of" in out


def test_verify_all_fresh_exits_0(tmp_path, capsys):
    home = tmp_path / "home"
    target = scaffold.create(home, "fresh-kb", owner="demo")
    (target / "facts" / "t.md").write_text(
        f"---\ntopic: t\n---\n- [gotcha] Fresh #x (verified: {old_date(1)})\n",
        encoding="utf-8",
    )
    code, out, _ = run(capsys, "--home", str(home), "verify", "fresh-kb")
    assert code == 0
    assert "stale 0 of" in out


def test_verify_unknown_plugin(tmp_path, capsys):
    code, _, err = run(capsys, "--home", str(tmp_path / "h"), "verify", "ghost")
    assert code == 1
    assert "mneme:" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_cli_verify.py -v`
Expected: FAIL — argparse `invalid choice: 'verify'`.

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

Parser: 

```python
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("plugin")
    p_verify.add_argument("--days", type=int, default=90)
```

Dispatch: `if args.command == "verify": return _verify_cmd(home, args)`.

Handler:

```python
def _verify_cmd(home: Path, args: argparse.Namespace) -> int:
    from datetime import date, datetime, timezone

    from . import registry as registry_mod
    from . import units as units_mod

    plugin = registry_mod.get_plugin(home, args.plugin)
    if plugin is None:
        raise MnemeError(f"plugin not registered: {args.plugin}")
    repo = Path(plugin.path)
    today = datetime.now(timezone.utc).date()

    def age(date_str: str) -> int | None:
        try:
            return (today - date.fromisoformat(date_str)).days
        except ValueError:
            return None

    total = 0
    stale: list[tuple[str, str, str]] = []

    skills_dir = repo / "skills"
    if skills_dir.is_dir():
        for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            total += 1
            try:
                meta, _ = units_mod.parse_frontmatter(skill_md.read_text(encoding="utf-8-sig"))
            except MnemeError:
                stale.append((units_mod.skill_unit_id(d.name), "none", "unknown"))
                continue
            md = meta.get("metadata", {})
            verified = str(md.get("mneme-last-verified", "")) if isinstance(md, dict) else ""
            a = age(verified) if verified else None
            if a is None or a > args.days:
                stale.append(
                    (units_mod.skill_unit_id(d.name), verified or "none",
                     str(a) if a is not None else "unknown")
                )

    facts_dir = repo / "facts"
    if facts_dir.is_dir():
        for f in sorted(facts_dir.glob("*.md")):
            try:
                _meta, body = units_mod.parse_frontmatter(f.read_text(encoding="utf-8-sig"))
            except MnemeError:
                continue
            for n, line in enumerate(body.splitlines(), start=1):
                if not line.startswith("- ["):
                    continue
                try:
                    b = units_mod.parse_bullet_line(line, n)
                except MnemeError:
                    continue
                total += 1
                a = age(b.verified) if b.verified else None
                if a is None or a > args.days:
                    stale.append(
                        (units_mod.fact_unit_id(f.stem, b.text), b.verified or "none",
                         str(a) if a is not None else "unknown")
                    )

    for unit_id, verified, age_days in stale:
        print(f"{unit_id}  last-verified={verified}  age-days={age_days}")
    print(f"stale {len(stale)} of {total} units")
    return 2 if stale else 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_cli_verify.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_cli_verify.py
git commit -m "feat: mneme verify — staleness sweep"
```

---

### Task 10: `mneme registry add --clone`

**Files:**
- Modify: `core/mneme_core/cli.py` (registry add handler)
- Create: `tests/core/test_registry_clone.py`

**Interfaces:**
- Consumes: `gitops.git` (works with any directory when passed the parent), `registry.add_plugin`, `paths.repos_dir`.
- Produces: `mneme registry add NAME --repo URL --clone [...]` — when `--clone` is given and the target path does not exist, runs `git clone <URL> <path>` (via a `subprocess` call with the same error contract as `gitops.git`) BEFORE registering; a failed clone raises `MnemeError` and registers nothing (atomic). When the path already exists, `--clone` is a no-op with a printed note. Without `--clone`, behavior is unchanged. Works with any git URL the user can access — GitHub SSH/HTTPS, GitHub Enterprise, GitLab, or local paths (tests use local repos as stand-ins; no network in tests).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_registry_clone.py`:

```python
import subprocess

from mneme_core import registry
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_source_repo(root):
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "MNEME.md").write_text(
        "# kb\n\n## Scope statement\n\nExisting team knowledge.\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(root),
         "add", "-A"], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(root),
         "commit", "-m", "seed"], check=True, capture_output=True,
    )
    return root


def test_add_with_clone_clones_and_registers(tmp_path, capsys):
    home = tmp_path / "home"
    src = make_source_repo(tmp_path / "upstream" / "team-kb")
    code, out, _ = run(
        capsys, "--home", str(home), "registry", "add", "team-kb",
        "--repo", str(src), "--clone",
    )
    assert code == 0
    assert "cloned" in out and "registered team-kb" in out
    p = registry.get_plugin(home, "team-kb")
    assert p is not None
    assert (tmp_path / "home" / "repos" / "team-kb" / "MNEME.md").exists()


def test_failed_clone_registers_nothing(tmp_path, capsys):
    home = tmp_path / "home"
    code, _, err = run(
        capsys, "--home", str(home), "registry", "add", "ghost-kb",
        "--repo", str(tmp_path / "does-not-exist"), "--clone",
    )
    assert code == 1
    assert "mneme:" in err
    assert registry.get_plugin(home, "ghost-kb") is None


def test_clone_noop_when_path_exists(tmp_path, capsys):
    home = tmp_path / "home"
    existing = tmp_path / "checkout"
    existing.mkdir()
    code, out, _ = run(
        capsys, "--home", str(home), "registry", "add", "local-kb",
        "--repo", "git@example.com:x.git", "--path", str(existing), "--clone",
    )
    assert code == 0
    assert "already exists" in out
    assert registry.get_plugin(home, "local-kb") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_registry_clone.py -v`
Expected: FAIL — `--clone` unrecognized argument.

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

Add `p_add.add_argument("--clone", action="store_true")` to the registry-add subparser. In `_registry_cmd`'s add branch, before `registry.add_plugin`:

```python
        plugin_path = args.path or str(paths.repos_dir(home) / args.name)
        if args.clone:
            target = Path(plugin_path)
            if target.exists():
                print(f"clone skipped: {target} already exists")
            else:
                import subprocess as subprocess_mod

                paths.ensure_layout(home)
                result = subprocess_mod.run(
                    ["git", "clone", args.repo, str(target)],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    raise MnemeError(
                        f"git clone failed: {result.stderr.strip()[:300]}"
                    )
                print(f"cloned {args.repo} -> {target}")
```

(then the existing `registry.add_plugin(...)` call proceeds with `plugin_path`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_registry_clone.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_registry_clone.py
git commit -m "feat: registry add --clone for existing remote repos"
```

---

### Task 11: `mneme adopt` — retrofit existing repos

**Files:**
- Modify: `core/mneme_core/scaffold.py`, `core/mneme_core/cli.py`
- Create: `tests/core/test_adopt.py`

**Interfaces:**
- Consumes: `templates` (MNEME_MD, CONTRIBUTING_MD, CODEOWNERS, VALIDATE_YML, RELEASE_YML, PLUGIN_JSON, MARKETPLACE_JSON, INDEX_SKILL_MD, render, render_json), `registry.get_plugin`, `lint.lint_repo`.
- Produces: `scaffold.adopt(home: Path, name: str, *, description: str = "", owner: str = "maintainers") -> list[str]` — for an already-registered plugin, adds ONLY missing governance artifacts to its clone and returns the list of relative paths written: `MNEME.md`, `CONTRIBUTING.md`, `CODEOWNERS`, `.github/workflows/validate.yml`, `.github/workflows/release.yml`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `skills/knowledge-index/SKILL.md` (+ regeneration), `facts/.gitkeep`. **Never overwrites an existing file.** Sensitivity/mode substitutions come from the registry entry. Description defaults to the plugin name sentence when not given. Nothing is committed — the repo's owners review and commit through their own process (the function prints nothing; the CLI reports). After writing, run `lint.lint_repo` and RETURN normally even when pre-existing content has lint errors — adoption must not be blocked by legacy content; the CLI surfaces the error count as a warning line instead. CLI: `mneme adopt NAME [--description D] [--owner O]` printing one `added: <relpath>` line each, `nothing to add` when complete, and `warning: existing content has N lint error(s) — run: mneme lint <path>` when applicable. Unknown plugin or missing clone → `MnemeError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_adopt.py`:

```python
from pathlib import Path

from mneme_core import registry, scaffold
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_existing_plugin(tmp_path, home):
    repo = tmp_path / "existing-kb"
    d = repo / "skills" / "legacy-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: legacy-skill\ndescription: A hand-written team skill\n---\nBody\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# existing\n", encoding="utf-8")
    registry.add_plugin(
        home,
        Plugin(name="existing-kb", repo="git@example.com:kb.git", path=str(repo),
               sensitivity="restricted", mode="pr"),
    )
    return repo


def test_adopt_adds_only_missing(tmp_path, capsys):
    home = tmp_path / "home"
    repo = make_existing_plugin(tmp_path, home)
    added = scaffold.adopt(home, "existing-kb", owner="team-leads")
    assert "MNEME.md" in added
    assert ".claude-plugin/plugin.json" in added
    assert "skills/knowledge-index/SKILL.md" in added
    # never overwrites
    assert (repo / "README.md").read_text(encoding="utf-8") == "# existing\n"
    # registry sensitivity flows into MNEME.md
    text = (repo / "MNEME.md").read_text(encoding="utf-8")
    assert "restricted" in text
    assert "* @team-leads" in (repo / "CODEOWNERS").read_text(encoding="utf-8")


def test_adopt_is_idempotent(tmp_path, capsys):
    home = tmp_path / "home"
    make_existing_plugin(tmp_path, home)
    scaffold.adopt(home, "existing-kb")
    assert scaffold.adopt(home, "existing-kb") == []


def test_adopt_never_touches_existing_mneme_md(tmp_path, capsys):
    home = tmp_path / "home"
    repo = make_existing_plugin(tmp_path, home)
    (repo / "MNEME.md").write_text("# custom scope\n", encoding="utf-8")
    added = scaffold.adopt(home, "existing-kb")
    assert "MNEME.md" not in added
    assert (repo / "MNEME.md").read_text(encoding="utf-8") == "# custom scope\n"


def test_adopt_cli_reports(tmp_path, capsys):
    home = tmp_path / "home"
    make_existing_plugin(tmp_path, home)
    code, out, _ = run(capsys, "--home", str(home), "adopt", "existing-kb", "--owner", "x-team")
    assert code == 0
    assert "added: MNEME.md" in out
    code, out, _ = run(capsys, "--home", str(home), "adopt", "existing-kb")
    assert "nothing to add" in out


def test_adopt_unknown_or_missing_clone(tmp_path, capsys):
    home = tmp_path / "home"
    code, _, err = run(capsys, "--home", str(home), "adopt", "ghost")
    assert code == 1
    registry.add_plugin(home, Plugin(name="gone-kb", repo="r", path=str(tmp_path / "nope")))
    code, _, err = run(capsys, "--home", str(home), "adopt", "gone-kb")
    assert code == 1


def test_adopt_warns_on_legacy_lint_errors(tmp_path, capsys):
    home = tmp_path / "home"
    repo = make_existing_plugin(tmp_path, home)
    bad = repo / "skills" / "broken-skill"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: Wrong_Name\n---\n", encoding="utf-8")
    code, out, _ = run(capsys, "--home", str(home), "adopt", "existing-kb")
    assert code == 0
    assert "warning:" in out and "lint error" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_adopt.py -v`
Expected: FAIL — `AttributeError: module 'mneme_core.scaffold' has no attribute 'adopt'`.

- [ ] **Step 3: Implement**

Append to `core/mneme_core/scaffold.py` (READ the post-audit file first — manifests render through `render_json`; mirror `create()`'s current rendering calls exactly):

```python
def adopt(
    home: Path, name: str, *, description: str = "", owner: str = "maintainers"
) -> list[str]:
    plugin = registry.get_plugin(home, name)
    if plugin is None:
        raise MnemeError(f"plugin not registered: {name}")
    target = Path(plugin.path)
    if not target.is_dir():
        raise MnemeError(f"local clone missing: {target}")
    if not description:
        description = f"Institutional knowledge maintained with mneme: {name}."
    subs = dict(
        name=name, description=description, owner=owner,
        sensitivity=plugin.sensitivity, mode=plugin.mode,
    )
    candidates = {
        "MNEME.md": templates.render(templates.MNEME_MD, **subs),
        "CONTRIBUTING.md": templates.render(templates.CONTRIBUTING_MD, **subs),
        "CODEOWNERS": templates.render(templates.CODEOWNERS, **subs),
        ".github/workflows/validate.yml": templates.VALIDATE_YML,
        ".github/workflows/release.yml": templates.RELEASE_YML,
        ".claude-plugin/plugin.json": templates.render_json(templates.PLUGIN_JSON, **subs),
        ".claude-plugin/marketplace.json": templates.render_json(templates.MARKETPLACE_JSON, **subs),
        "skills/knowledge-index/SKILL.md": templates.render(templates.INDEX_SKILL_MD, **subs),
        "facts/.gitkeep": "",
    }
    added: list[str] = []
    for rel, content in candidates.items():
        path = target / rel
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        added.append(rel)
    if "skills/knowledge-index/SKILL.md" in added:
        regenerate_index_skill(target, name, description)
    return added
```

In `core/mneme_core/cli.py`: parser `p_adopt = sub.add_parser("adopt")` with `name`, `--description` (default `""`), `--owner` (default `"maintainers"`); dispatch:

```python
        if args.command == "adopt":
            from . import lint as lint_mod
            from . import registry as registry_mod
            from . import scaffold as scaffold_mod

            added = scaffold_mod.adopt(
                home, args.name, description=args.description, owner=args.owner
            )
            for rel in added:
                print(f"added: {rel}")
            if not added:
                print("nothing to add")
            plugin = registry_mod.get_plugin(home, args.name)
            issues = lint_mod.lint_repo(Path(plugin.path))
            errors = [i for i in issues if i.severity == "error"]
            if errors:
                print(
                    f"warning: existing content has {len(errors)} lint error(s)"
                    f" — run: mneme lint {plugin.path}"
                )
            print("review and commit these files through your repo's normal process")
            return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_adopt.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/scaffold.py core/mneme_core/cli.py tests/core/test_adopt.py
git commit -m "feat: mneme adopt — retrofit governance onto existing repos"
```

---

### Task 12: Proposal size caps — carried Plan 04 minor

**Files:**
- Modify: `core/mneme_core/proposals.py`
- Create: `tests/core/test_proposal_caps.py`

**Interfaces:**
- Consumes: the post-audit `proposals.py` (it catches `RecursionError`/`ValueError` in `parse_proposals` — READ the current file and integrate).
- Produces: module constants `MAX_PROPOSALS = 100`, `MAX_RATIONALE = 2_000`, `MAX_PROCEDURE = 20_000`, `MAX_FAILURE_PATTERN = 20_000`, `MAX_FACT_TEXT = 2_000`, `MAX_TAGS = 20`, `MAX_TARGET = 100`, `MAX_TARGET_UNIT = 300`. `parse_proposals` raises `MnemeError` when the document carries more than `MAX_PROPOSALS` entries; `_validate` rejects (per-proposal error, others unaffected) any field over its cap with a message naming the field and limit. Boundary values (exactly at the cap) pass.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_proposal_caps.py`:

```python
import json

import pytest

from mneme_core import proposals
from mneme_core.errors import MnemeError


def fact_entry(**kw):
    entry = dict(
        type="fact", edit="new", target="acme-knowledge",
        topic="staging-env", category="constraint",
        text="DB resets nightly", tags=["staging"],
        confidence=0.7, rationale="observed",
    )
    entry.update(kw)
    return entry


def skill_entry(**kw):
    entry = dict(
        type="skill", edit="new", target="acme-knowledge",
        name="deploy-widget", description="Use when deploying widgets",
        procedure="Steps.", failure_pattern="What failed.",
        confidence=0.8, rationale="verified",
    )
    entry.update(kw)
    return entry


def parse(entries):
    return proposals.parse_proposals(json.dumps({"proposals": entries}))


def test_document_cap():
    with pytest.raises(MnemeError):
        parse([fact_entry() for _ in range(proposals.MAX_PROPOSALS + 1)])
    valid, errors = parse([fact_entry()] * proposals.MAX_PROPOSALS)
    assert errors == []
    assert len(valid) == proposals.MAX_PROPOSALS


@pytest.mark.parametrize(
    "entry_kwargs, field",
    [
        ({"rationale": "x" * (proposals.MAX_RATIONALE + 1)}, "rationale"),
        ({"text": "x" * (proposals.MAX_FACT_TEXT + 1)}, "text"),
        ({"tags": ["t"] * (proposals.MAX_TAGS + 1)}, "tags"),
        ({"target": "t" * (proposals.MAX_TARGET + 1)}, "target"),
    ],
)
def test_fact_field_caps(entry_kwargs, field):
    valid, errors = parse([fact_entry(**entry_kwargs)])
    assert valid == []
    assert len(errors) == 1
    assert field in errors[0]


@pytest.mark.parametrize(
    "entry_kwargs, field",
    [
        ({"procedure": "x" * (proposals.MAX_PROCEDURE + 1)}, "procedure"),
        ({"failure_pattern": "x" * (proposals.MAX_FAILURE_PATTERN + 1)}, "failure_pattern"),
        (
            {"edit": "update", "target_unit": "skills/" + "x" * proposals.MAX_TARGET_UNIT},
            "target_unit",
        ),
    ],
)
def test_skill_field_caps(entry_kwargs, field):
    valid, errors = parse([skill_entry(**entry_kwargs)])
    assert valid == []
    assert len(errors) == 1
    assert field in errors[0]


def test_boundary_values_pass():
    valid, errors = parse(
        [
            fact_entry(text="x" * proposals.MAX_FACT_TEXT, rationale="r" * proposals.MAX_RATIONALE),
            skill_entry(procedure="p" * proposals.MAX_PROCEDURE),
        ]
    )
    assert errors == []
    assert len(valid) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_proposal_caps.py -v`
Expected: FAIL — `AttributeError: module 'mneme_core.proposals' has no attribute 'MAX_PROPOSALS'`.

- [ ] **Step 3: Modify `core/mneme_core/proposals.py`**

Add the constants after the existing `_MAX_DESCRIPTION`:

```python
MAX_PROPOSALS = 100
MAX_RATIONALE = 2_000
MAX_PROCEDURE = 20_000
MAX_FAILURE_PATTERN = 20_000
MAX_FACT_TEXT = 2_000
MAX_TAGS = 20
MAX_TARGET = 100
MAX_TARGET_UNIT = 300
```

In `parse_proposals`, after the top-level shape check:

```python
    if len(data["proposals"]) > MAX_PROPOSALS:
        raise MnemeError(
            f"proposals document has {len(data['proposals'])} entries; max {MAX_PROPOSALS}"
        )
```

In `_validate`, add a small helper and apply it (common fields right where they are parsed; type-specific fields in their branches):

```python
def _cap(value: str, limit: int, field: str) -> str:
    if len(value) > limit:
        raise MnemeError(f"{field} exceeds {limit} chars ({len(value)})")
    return value
```

- common: `target = _cap(str(entry.get("target") or UNASSIGNED), MAX_TARGET, "target")`; `target_unit = _cap(str(entry.get("target_unit", "")), MAX_TARGET_UNIT, "target_unit")`; `rationale = _cap(str(entry.get("rationale", "")), MAX_RATIONALE, "rationale")`.
- skill branch: `p.procedure = _cap(..., MAX_PROCEDURE, "procedure")`, `p.failure_pattern = _cap(..., MAX_FAILURE_PATTERN, "failure_pattern")`.
- fact branch: `p.text = _cap(..., MAX_FACT_TEXT, "text")`; after building `p.tags`: `if len(p.tags) > MAX_TAGS: raise MnemeError(f"tags exceeds {MAX_TAGS} entries ({len(p.tags)})")`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_proposal_caps.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/proposals.py tests/core/test_proposal_caps.py
git commit -m "fix: size caps on untrusted proposal fields"
```

---

## Verification (end of plan)

1. `python3 -m pytest -v` — entire suite green.
2. Full loop, PR mode against a local bare remote (no network, no gh):
   ```bash
   export MNEME_HOME=$(mktemp -d)
   WORK=$(mktemp -d)
   bin/mneme init
   bin/mneme new loop-knowledge --owner demo
   KB=$(python3 -c "import json,os;print(json.load(open(os.environ['MNEME_HOME']+'/registry.json'))['plugins'][0]['path'])")
   git init --bare "$WORK/remote.git"
   git -C "$KB" remote add origin "$WORK/remote.git" && git -C "$KB" push -u origin main
   cat > "$WORK/props.json" <<'EOF'
   {"proposals": [{"type": "fact", "edit": "new", "target": "loop-knowledge", "topic": "demo",
     "category": "gotcha", "text": "Demo gotcha for the loop", "tags": ["demo"],
     "confidence": 0.8, "rationale": "verified"}]}
   EOF
   bin/mneme distill ingest "$WORK/props.json" --source demo@e2e
   bin/mneme share list
   ID=$(bin/mneme stage list | awk '{print $1}')
   bin/mneme share diff "$ID"
   bin/mneme share apply --ids "$ID"        # harvested … on mneme/harvest-…; pr: manual: …
   git -C "$WORK/remote.git" branch          # harvest branch arrived on the "remote"
   git -C "$KB" log main..$(git -C "$KB" branch --list 'mneme/harvest-*' --format='%(refname:short)') --format=%B | grep Mneme-Source
   bin/mneme stage list                      # empty
   bin/mneme verify loop-knowledge           # exit 0 (fresh)
   ```
3. `git log --oneline` on the mneme repo shows one commit per task (12 new commits).

## Out of scope for Plan 05 (later plans)

- The conversational review UX (`/mneme:share` command + harvesting skill driving list→diff→approve→apply) — Plan 06.
- Hook wiring, distiller agent, plugin manifest for mneme itself — Plan 06.
- E2E harness + dogfood knowledge repo — Plan 07.
- Re-verification of stale units (agent work over the `mneme verify` worklist) — Plan 06/07.
