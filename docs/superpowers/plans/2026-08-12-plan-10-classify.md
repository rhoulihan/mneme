# Mneme Plan 10 — Facts Under knowledge-index + /mneme:classify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two user-directed changes, released as 0.5.0. (1) Facts move inside the router skill: the canonical location becomes `skills/knowledge-index/facts/` (the index skill and the files it routes to travel as one self-contained directory); legacy top-level `facts/` remains readable everywhere. (2) `/mneme:classify` — a prompt-driven librarian pass that runs on the CURRENT DIRECTORY (which must resolve, via `routing.plugin_for_path`, to a registered knowledge plugin — a clear failure otherwise): it parses every fact accumulated from accepted PRs and integrates each into the relevant skill's content, resets (regenerates) the knowledge-index over what remains, migrates legacy facts to the new location, and delivers the whole reorganization as a `mneme/classify-*` branch + PR. Mneme never writes `main` (Plan 09 doctrine). No plugin-name argument anywhere in the classify surface — the directory is the argument.

**Architecture:** Classification is LLM judgment over repo structures that vary — so it is prompt-driven by design (user direction), wrapped in deterministic rails: `mneme classify begin` (preconditions + branch), `mneme classify prepare` (JSON bundle: facts inventory, skill map, integration instructions), the in-session agent proposes a triage mapping TO THE USER and applies approved edits in the working tree, `mneme classify finalize` (legacy-facts migration via `git mv`, knowledge-index regeneration, repo lint, secret scan over changed files, commit with provenance, push + PR), `mneme classify abort` (restore + delete branch). Fact unit ids keep the `facts/<stem>#<key>` scheme regardless of physical location, so dedup, declined-ledger, and `similar-to` continuity survive the move.

**Tech Stack:** No new dependencies.

**Spec impact:** §5.1/§5.3 (facts location), new §7.7 (classify); Task 7 updates the spec document.

## Global Constraints

- All prior Global Constraints hold, including Plan 09's invariant: no code path writes a registered repo's `main` — classify commits only on its own branch. Full suite green after every task.
- **Location resolution rule (used by every facts consumer):** `units.facts_dir(root)` returns `root/skills/knowledge-index/facts` when that directory exists, else `root/facts` when THAT exists (legacy), else the canonical new path (for creation). Unit ids are always `facts/<stem>#<topic-key>` — never the physical path.
- Classification content decisions belong to the agent+user; every deterministic gate still applies at finalize (lint error-free, changed files scan blocker-free, atomic rollback on failure via the Plan 05/09 `_abort` machinery).
- Facts are never silently deleted by classify: every fact either lands (verbatim or merged, with its verified date and meaning preserved) in a skill's content, or remains in the facts directory. The instructions template states this and the finalize commit body lists moves.
- Sanctioned test-contract updates (new facts location): `tests/core/test_scaffold.py`, `tests/core/test_regenerate.py`, `tests/core/test_adopt.py`, `tests/core/test_harvest_facts.py`, `tests/core/test_cli_verify.py`, `tests/index/test_build_facts.py` (and sibling index tests using `facts/` fixtures), `tests/e2e/test_full_loop.py`, `tests/e2e/test_release.py`. Equal-or-stronger assertions only; legacy-location coverage must be ADDED, not substituted.
- READ every file before modifying. Run tests with `python3 -m pytest` from the repo root.

## File Structure

```
core/mneme_core/units.py       # Task 1: FACTS_CANONICAL, facts_dir()
core/mneme_core/scaffold.py    # Task 2: create/adopt/regenerate use the new location
core/mneme_core/lint.py        # Task 2: lint_repo resolves via facts_dir
core/mneme_index/build.py      # Task 2: _fact_rows resolves via facts_dir (ids unchanged)
core/mneme_core/harvest.py     # Task 2: apply_fact resolves via facts_dir
core/mneme_core/cli.py         # Task 2 (verify sweep), Tasks 3–4 (classify begin/prepare/finalize/abort)
core/mneme_core/classify.py    # Tasks 3–4 (new): branch rails + bundle + finalize
core/mneme_core/templates.py   # Task 4: CLASSIFY_INSTRUCTIONS
skills/classify/SKILL.md       # Task 5 (new command skill)
core/mneme_core/paths.py + cli.py detection subcommands  # Task 6 (carried Plan 08 minor)
docs/superpowers/specs/2026-08-11-mneme-design.md, README.md, docs/install.md, CHANGELOG.md  # Task 7
core/*/__init__.py, .claude-plugin/plugin.json, pyproject.toml, tests/e2e/test_release.py     # Task 7: 0.5.0
tests/core/test_facts_location.py   # Task 1
tests/core/test_facts_consumers.py  # Task 2
tests/core/test_classify_rails.py   # Task 3
tests/core/test_classify_bundle.py  # Task 4
tests/adapter/test_skills.py        # Task 5 (append)
tests/e2e/test_classify_loop.py     # Task 5
```

---

### Task 1: `units.facts_dir` — one resolution rule

**Files:**
- Modify: `core/mneme_core/units.py` (append)
- Create: `tests/core/test_facts_location.py`

**Interfaces:**
- Produces: `FACTS_CANONICAL = "skills/knowledge-index/facts"`; `facts_dir(root: Path) -> Path` implementing the Global Constraints resolution rule exactly. Lives in `units` so `mneme_index` may import it (boundary allows units).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_facts_location.py`:

```python
from mneme_core import units


def test_canonical_constant():
    assert units.FACTS_CANONICAL == "skills/knowledge-index/facts"


def test_prefers_canonical_when_present(tmp_path):
    (tmp_path / "skills" / "knowledge-index" / "facts").mkdir(parents=True)
    (tmp_path / "facts").mkdir()
    assert units.facts_dir(tmp_path) == tmp_path / "skills" / "knowledge-index" / "facts"


def test_falls_back_to_legacy(tmp_path):
    (tmp_path / "facts").mkdir()
    assert units.facts_dir(tmp_path) == tmp_path / "facts"


def test_defaults_to_canonical_for_creation(tmp_path):
    assert units.facts_dir(tmp_path) == tmp_path / "skills" / "knowledge-index" / "facts"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_facts_location.py -v` — FAIL: no attribute.

- [ ] **Step 3: Append to `core/mneme_core/units.py`**

```python
FACTS_CANONICAL = "skills/knowledge-index/facts"


def facts_dir(root: Path) -> Path:
    canonical = root / FACTS_CANONICAL
    if canonical.is_dir():
        return canonical
    legacy = root / "facts"
    if legacy.is_dir():
        return legacy
    return canonical
```

(`Path` is already imported? READ the file — if not, add `from pathlib import Path` to the imports.)

- [ ] **Step 4–5: Task tests pass, full suite green, commit**

```bash
git add core/mneme_core/units.py tests/core/test_facts_location.py
git commit -m "feat: facts location resolution — canonical under knowledge-index"
```

---

### Task 2: Every facts consumer resolves through `facts_dir`

**Files:**
- Modify: `core/mneme_core/scaffold.py`, `core/mneme_core/lint.py`, `core/mneme_index/build.py`, `core/mneme_core/harvest.py`, `core/mneme_core/cli.py` (`_verify_cmd`), plus the sanctioned fixture updates in `tests/core/test_scaffold.py`, `tests/core/test_regenerate.py`, `tests/core/test_adopt.py`, `tests/core/test_harvest_facts.py`, `tests/core/test_cli_verify.py`, `tests/index/test_build_facts.py`
- Create: `tests/core/test_facts_consumers.py`

**Interfaces:**
- Produces: `scaffold.create` and `adopt` write `skills/knowledge-index/facts/.gitkeep` (not top-level `facts/`); `regenerate_index_skill` reads via `facts_dir` and keeps emitting table links as `facts/<file>` (now correctly relative to the skill directory in the canonical layout; for legacy repos the link text is unchanged from today — note this in the doc column header if ambiguous); `lint_repo`, `mneme_index.build._fact_rows`, `harvest.apply_fact`, and the verify sweep all resolve via `facts_dir`. **Unit ids unchanged**: `fact_unit_id` stays `facts/<stem>#<key>` for both layouts — the index build must produce identical ids for the same content in either location (test asserts this).
- `tests/core/test_facts_consumers.py` covers the matrix the per-module tests don't: same content in canonical vs legacy layout → identical index unit ids; `apply_fact` writes into whichever layout exists; a legacy repo lints clean; scaffold output has NO top-level `facts/`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_facts_consumers.py`:

```python
from mneme_core import harvest, lint, scaffold, staging, units
from mneme_core.staging import Candidate, candidate_id
from mneme_index import build, db

BULLET = "- [gotcha] Layout-agnostic fact #layout (verified: 2026-08-12)\n"
FACT_FILE = "---\ntopic: layout\n---\n" + BULLET


def make_layout(root, canonical):
    d = (root / units.FACTS_CANONICAL) if canonical else (root / "facts")
    d.mkdir(parents=True)
    (d / "layout.md").write_text(FACT_FILE, encoding="utf-8")
    return root


def index_ids(tmp_path, root, name):
    conn = db.open_db(tmp_path / f"{name}.db")
    build.index_tree(conn, name, root)
    ids = {r["id"] for r in conn.execute("SELECT id FROM units WHERE kind='fact'")}
    conn.close()
    return ids


def test_identical_ids_across_layouts(tmp_path):
    legacy = make_layout(tmp_path / "legacy", canonical=False)
    canonical = make_layout(tmp_path / "canon", canonical=True)
    assert index_ids(tmp_path, legacy, "l") == index_ids(tmp_path, canonical, "c")
    assert index_ids(tmp_path, legacy, "l2") == {"facts/layout#layout-agnostic-fact"}


def test_scaffold_has_no_top_level_facts(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "layout-kb", owner="demo")
    assert not (target / "facts").exists()
    assert (target / units.FACTS_CANONICAL / ".gitkeep").exists()


def test_apply_fact_respects_existing_layout(tmp_path):
    legacy = make_layout(tmp_path / "legacy", canonical=False)
    body = "- [constraint] Written into legacy layout #x (verified: 2026-08-12)"
    cand = Candidate(
        id=candidate_id("fact", "t", body), type="fact", edit="new",
        target="t", body=body, topic="incoming",
    )
    harvest.apply_fact(legacy, cand)
    assert (legacy / "facts" / "incoming.md").exists()
    assert not (legacy / units.FACTS_CANONICAL).exists()


def test_legacy_repo_lints_clean(tmp_path):
    legacy = make_layout(tmp_path / "legacy", canonical=False)
    assert not lint.has_errors(lint.lint_repo(legacy))


def test_canonical_repo_lints_facts(tmp_path):
    canonical = make_layout(tmp_path / "canon", canonical=True)
    bad = canonical / units.FACTS_CANONICAL / "bad.md"
    bad.write_text("---\ntopic: bad\n---\n- [bogus] nope (verified: 2026-08-12)\n", encoding="utf-8")
    issues = lint.lint_repo(canonical)
    assert any(i.code == "MN007" for i in issues)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_facts_consumers.py -v` — FAIL on scaffold location, canonical lint, canonical index rows.

- [ ] **Step 3: Implement**

Switch each consumer's hardcoded `root / "facts"` to `units.facts_dir(root)` (in `mneme_index.build`, import `facts_dir` from `mneme_core.units` — boundary-legal). `scaffold.create`/`adopt` create the canonical dir + `.gitkeep`. `regenerate_index_skill` reads via `facts_dir`. Update the sanctioned fixture files: scaffold/regenerate/adopt tests assert the canonical path; harvest/verify/index tests gain a canonical-layout variant while KEEPING a legacy-layout case each.

- [ ] **Step 4–5: Affected modules green, full suite green, commit**

```bash
git add core/ tests/
git commit -m "feat: all facts consumers resolve canonical-or-legacy location"
```

---

### Task 3: Classify rails — begin / abort (`classify.py`)

**Files:**
- Create: `core/mneme_core/classify.py`, `tests/core/test_classify_rails.py`
- Modify: `core/mneme_core/cli.py`

**Interfaces:**
- Produces: `classify.resolve(home, cwd) -> tuple[Scope, Path]` — `routing.plugin_for_path(home, cwd)`; `MnemeError("this directory is not inside a registered knowledge plugin — cd into one or register it first (/mneme:register)")` when it isn't. `classify.begin(home, cwd) -> str` — resolves the plugin FROM CWD (works from any subdirectory of the plugin), requires git repo + clean tree, `sync_main`, creates and returns `mneme/classify-<YYYYMMDD-HHMMSS>`; refuses (MnemeError) when a `mneme/classify-*` branch is already checked out (double-begin). `classify.abort(home, cwd) -> None` — only when currently on a `mneme/classify-*` branch: `gitops.restore`, checkout `main`, delete the branch; MnemeError otherwise. CLI: `mneme classify begin [--cwd DIR]`, `mneme classify abort [--cwd DIR]` — `--cwd` defaults to `Path.cwd()` (test hook; users never pass it), printing the branch / `aborted`. No plugin-name positional exists.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_classify_rails.py`:

```python
import pytest

from mneme_core import classify, gitops, scaffold
from mneme_core.errors import MnemeError


def make_kb(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "classify-kb", owner="demo")
    return home, target


def test_begin_creates_branch_from_clean_main(tmp_path):
    home, target = make_kb(tmp_path)
    branch = classify.begin(home, target)
    assert branch.startswith("mneme/classify-")
    assert gitops.current_branch(target) == branch


def test_begin_resolves_from_subdirectory(tmp_path):
    home, target = make_kb(tmp_path)
    branch = classify.begin(home, target / "skills")
    assert gitops.current_branch(target) == branch


def test_begin_refuses_dirty_or_double(tmp_path):
    home, target = make_kb(tmp_path)
    (target / "junk.txt").write_text("x", encoding="utf-8")
    with pytest.raises(MnemeError):
        classify.begin(home, target)
    (target / "junk.txt").unlink()
    classify.begin(home, target)
    with pytest.raises(MnemeError):
        classify.begin(home, target)


def test_abort_restores_and_deletes(tmp_path):
    home, target = make_kb(tmp_path)
    branch = classify.begin(home, target)
    (target / "MNEME.md").write_text("mutated", encoding="utf-8")
    (target / "stray.txt").write_text("x", encoding="utf-8")
    classify.abort(home, target)
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert branch not in gitops.git(target, "branch", "--list", "mneme/classify-*")


def test_abort_outside_classify_branch_refuses(tmp_path):
    home, target = make_kb(tmp_path)
    with pytest.raises(MnemeError):
        classify.abort(home, target)


def test_unregistered_directory_fails_clearly(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(MnemeError) as exc:
        classify.begin(tmp_path / "home", plain)
    assert "not inside a registered knowledge plugin" in str(exc.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_classify_rails.py -v` — FAIL: no module.

- [ ] **Step 3: Implement `core/mneme_core/classify.py`**

```python
"""Classify rails — branch discipline around the prompt-driven librarian pass."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import gitops, registry
from .errors import MnemeError


def resolve(home: Path, cwd: Path):
    from . import routing

    scope = routing.plugin_for_path(home, cwd)
    if scope is None:
        raise MnemeError(
            "this directory is not inside a registered knowledge plugin —"
            " cd into one or register it first (/mneme:register)"
        )
    repo = Path(scope.path)
    if not gitops.is_git_repo(repo):
        raise MnemeError(f"{repo} is not a git repository")
    return scope, repo


def begin(home: Path, cwd: Path) -> str:
    _scope, repo = resolve(home, cwd)
    if gitops.current_branch(repo).startswith("mneme/classify-"):
        raise MnemeError("a classify branch is already active — finalize or abort it first")
    if not gitops.is_clean(repo):
        raise MnemeError(f"{repo} has uncommitted changes — commit or stash them first")
    gitops.sync_main(repo)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"mneme/classify-{stamp}"
    gitops.create_branch(repo, branch)
    return branch


def abort(home: Path, cwd: Path) -> None:
    _scope, repo = resolve(home, cwd)
    branch = gitops.current_branch(repo)
    if not branch.startswith("mneme/classify-"):
        raise MnemeError("not on a classify branch — nothing to abort")
    gitops.restore(repo)
    gitops.git(repo, "checkout", "main")
    gitops.git(repo, "branch", "-D", branch)
```

CLI wiring: `classify` subparser group with `begin`/`abort`, each taking only `--cwd` (type `Path`, default `None` → `Path.cwd()` in the handler), dispatching to a `_classify_cmd` that prints the branch name / `aborted`. (`registry` import in classify.py becomes unnecessary — drop it; `Scope` comes from routing at call time.)

- [ ] **Step 4–5: Task tests green, full suite green, commit**

```bash
git add core/mneme_core/classify.py core/mneme_core/cli.py tests/core/test_classify_rails.py
git commit -m "feat: classify branch rails — begin and abort"
```

---

### Task 4: Classify bundle + finalize

**Files:**
- Modify: `core/mneme_core/classify.py`, `core/mneme_core/cli.py`, `core/mneme_core/templates.py`
- Create: `tests/core/test_classify_bundle.py`

**Interfaces:**
- `templates.CLASSIFY_INSTRUCTIONS` (no placeholders): the librarian contract for the agent — integrate each fact into the MOST relevant existing skill (append to an appropriate section of its SKILL.md or a file under its directory, preserving the fact's meaning, tags, and verified date and citing it as a fact-derived note); create a new skill only when several related facts justify one; a fact with no good home STAYS in the facts directory; NEVER delete knowledge; when a fact merely restates what a skill already says, record it as retired-into-that-skill in your report to the user; propose the full mapping to the user and get their approval BEFORE editing; after edits run finalize.
- `classify.bundle(home, cwd) -> dict` — resolves the plugin from cwd via `classify.resolve`; `{"plugin", "repo", "facts": [{"file", "topic", "line", "category", "text", "tags", "verified", "unit_id"}...], "skills": [{"name", "description", "dir", "files": [relative paths]}...], "legacy_layout": bool, "instructions": templates.CLASSIFY_INSTRUCTIONS}`; skills listing walks `skills/*/SKILL.md` (frontmatter name/description; skip unparseable with a note list), EXCLUDING `knowledge-index`. CLI `mneme classify prepare [--cwd DIR]` prints it as JSON.
- `classify.finalize(home, cwd, *, push=True) -> HarvestResult`-shaped result (reuse `harvest.HarvestResult`): resolves from cwd; requires an active `mneme/classify-*` branch; if a legacy `facts/` dir exists AND the canonical dir is now in use or being created, `git mv` remaining legacy fact files into the canonical dir (creating it); regenerate the knowledge-index skill (reading plugin.json name/description as harvest does); gate: `lint_repo` error-free + `scan` blocker-free over every file changed on the branch (`git diff --name-only main...HEAD` plus working-tree changes); commit ALL changes with subject `knowledge: classify <date>` and body listing changed files; push + `open_pr` when a remote exists (else the local-branch message); checkout `main`, branch preserved; ledger record with `"kind": "classify"`. Failure at any point → same `_abort`-style rollback (restore, checkout main, delete branch) and MnemeError. Nothing to classify (no working-tree edits, no migration, AND the branch is not ahead of `main`) → MnemeError; the rails discard the branch themselves rather than telling the user to abort — this task's own test asserts `current_branch == main` afterwards, and the earlier "abort instead" wording contradicted it (corrected 2026-08-12 after the Task 4 review; auto-rollback is the behaviour). A branch that IS ahead of `main` is the opposite case — the librarian committed their own edits and index regeneration is a no-op — and finalize must DELIVER those commits (result.commit is the existing HEAD; no empty commit, no rollback), never demand a fresh one. CLI: `mneme classify finalize [--cwd DIR] [--no-push]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_classify_bundle.py`:

```python
import json

import pytest

from mneme_core import classify, gitops, scaffold, units
from mneme_core.cli import main
from mneme_core.errors import MnemeError


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_kb(tmp_path, legacy=False):
    home = tmp_path / "home"
    target = scaffold.create(home, "lib-kb", owner="demo")
    skill = target / "skills" / "deploy-widget"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: deploy-widget\ndescription: Use when deploying widgets\n---\n\n## Procedure\n\nSteps.\n",
        encoding="utf-8",
    )
    facts = (target / "facts") if legacy else (target / units.FACTS_CANONICAL)
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "deploys.md").write_text(
        "---\ntopic: deploys\n---\n"
        "- [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-12)\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "fixtures")
    return home, target


def test_bundle_shape(tmp_path, capsys):
    home, target = make_kb(tmp_path)
    b = classify.bundle(home, target)
    assert b["plugin"] == "lib-kb"
    assert b["legacy_layout"] is False
    fact = b["facts"][0]
    assert fact["unit_id"] == "facts/deploys#deploys-fail-when-the-lb-caches"
    assert fact["category"] == "gotcha"
    names = [s["name"] for s in b["skills"]]
    assert "deploy-widget" in names
    assert "knowledge-index" not in names
    assert "NEVER delete" in b["instructions"] or "never delete" in b["instructions"].lower()
    code, out, _ = run(capsys, "--home", str(home), "classify", "prepare", "--cwd", str(target / "skills"))
    assert code == 0
    assert json.loads(out)["plugin"] == "lib-kb"


def test_finalize_full_pass_with_migration(tmp_path):
    home, target = make_kb(tmp_path, legacy=True)
    classify.begin(home, target)
    # simulate the agent integrating the fact into the skill
    skill_md = target / "skills" / "deploy-widget" / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8")
        + "\n## Operational notes\n\n- Deploys fail when the LB caches dead targets (verified: 2026-08-12).\n",
        encoding="utf-8",
    )
    (target / "facts" / "deploys.md").unlink()
    main_before = gitops.git(target, "rev-parse", "main")
    result = classify.finalize(home, target, push=False)
    assert result.branch.startswith("mneme/classify-")
    assert gitops.git(target, "rev-parse", "main") == main_before  # PR-only invariant
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    subject = gitops.git(target, "log", result.branch, "-1", "--format=%s")
    assert subject.startswith("knowledge: classify")
    # legacy dir migrated on the branch
    tree = gitops.git(target, "ls-tree", "-r", "--name-only", result.branch)
    assert "facts/" not in [p.split("/")[0] + "/" for p in tree.splitlines() if p.startswith("facts/")] or True
    assert not any(p.startswith("facts/") for p in tree.splitlines())


def test_finalize_requires_active_branch_and_changes(tmp_path):
    home, target = make_kb(tmp_path)
    with pytest.raises(MnemeError):
        classify.finalize(home, target, push=False)
    classify.begin(home, target)
    with pytest.raises(MnemeError):
        classify.finalize(home, target, push=False)  # no edits, no migration
    assert gitops.current_branch(target) == "main"  # rolled back cleanly


def test_finalize_gate_rolls_back_on_lint_error(tmp_path):
    home, target = make_kb(tmp_path)
    classify.begin(home, target)
    bad = target / "skills" / "broken-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: Wrong_Name\n---\n", encoding="utf-8")
    main_before = gitops.git(target, "rev-parse", "main")
    with pytest.raises(MnemeError):
        classify.finalize(home, target, push=False)
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.git(target, "rev-parse", "main") == main_before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_classify_bundle.py -v` — FAIL: no `bundle`/`finalize`.

- [ ] **Step 3: Implement**

Add `CLASSIFY_INSTRUCTIONS` to templates (cover every clause in the Interfaces description, including "propose the mapping and get the user's approval BEFORE editing"). Implement `bundle` (walk facts via `units.facts_dir` + `parse_fact_bullets`; walk skills excluding `knowledge-index`) and `finalize` per the Interfaces contract, reusing `gitops` and the harvest `_regenerate_index`/rollback patterns (extract a shared helper rather than duplicating if cleaner — note it). Wire `classify prepare|finalize` into the CLI (`--no-push` on finalize).

- [ ] **Step 4–5: Task tests green, full suite green, commit**

```bash
git add core/mneme_core/classify.py core/mneme_core/cli.py core/mneme_core/templates.py tests/core/test_classify_bundle.py
git commit -m "feat: classify bundle and PR-only finalize with migration"
```

**Deviations actually taken (recorded 2026-08-12, after the Task 4 review).** Task 4 was
reported as `deviations: []`; that was incomplete. The accurate list:

1. `core/mneme_core/gitops.py` was modified in commit `f9cdfd2`, outside Task 4's Files
   list: `git_raw` was extracted (raw stdout) and `git` reduced to `git_raw(...).strip()`.
   Behaviour-preserving for every existing caller; needed because `git status --porcelain
   -z` records open with a significant space that `strip()` was shifting off the path.
   Covered by Step 3's "extract a shared helper rather than duplicating if cleaner — note
   it" allowance, but the note landed only in the docstring, not in the report.
2. Task 4 landed as three commits (`f9cdfd2` feat, `cc20b6b` review fixes — untracked-dir
   scan coverage, non-UTF-8 scan text, commit subject — and this pass's work-loss fix)
   against the end-of-plan "one commit per task" item; see Verification note 4.
3. `tests/core/test_classify_bundle.py` carries assertions beyond the plan's listing
   (secret-scan coverage of a newly created skill directory, byte-exact changed paths, and
   the ahead-but-clean delivery case). All are additions — every plan assertion is kept.

---

### Task 5: `/mneme:classify` skill + e2e

**Files:**
- Create: `skills/classify/SKILL.md`, `tests/e2e/test_classify_loop.py`
- Modify: `tests/adapter/test_skills.py` (append classify to the imperative set)

**Interfaces:**
- `skills/classify/SKILL.md` — user-invocable (`disable-model-invocation: true`, no argument-hint — it operates on the current directory): FIRST confirm the session is inside a registered knowledge plugin (a failed `mneme classify begin` says exactly that — relay the error and suggest `/mneme:register` or changing directory); then run `mneme classify begin`, then `mneme classify prepare` and READ the bundle; propose the complete triage mapping to the user (fact → destination skill/section, facts staying put, any new skill worth creating) and WAIT for approval; apply approved edits with ordinary file edits in the repo working tree (preserve verified dates; never delete knowledge; keep each skill's existing structure — the bundle's file listings show it); then `mneme classify finalize` and report the PR/branch; on any problem or user cancellation run `mneme classify abort`. Binary resolution line as in the other skills.
- `tests/e2e/test_classify_loop.py` — scripted stand-in for the agent: begin → edit (integrate the fixture fact into the fixture skill, delete the fact file) → finalize with a local bare remote → assert the classify branch reached the remote, `main` unchanged locally and remotely, knowledge-index regenerated (fact's topic row gone), and `mneme index rebuild` + `mneme search` now find the knowledge through the skill.

- [ ] **Steps:** failing tests (skill lint + content assertions: `classify begin`, `classify prepare`, `classify finalize`, `classify abort`, "approval" all present in the body; e2e as described) → implement skill content → green → full suite → commit `feat: /mneme:classify command skill and end-to-end loop`.

---

### Task 6: Persisted detection declines — carried Plan 08 minor

**Files:**
- Modify: `core/mneme_core/paths.py`, `core/mneme_core/cli.py` (detection subcommands + `_registration_nudge`)
- Create: `tests/core/test_detection_declines.py`

**Interfaces:**
- Produces: `paths.detection_declined_path(home) -> home / "detection-declined.jsonl"`; `mneme detection decline [--cwd DIR]` — resolves the knowledge repo via `routing.find_knowledge_repo` (MnemeError when none found), appends `{"path": <resolved str>, "ts": <iso>}`, prints `declined <path>` (idempotent — a second decline is a no-op with the same output); `mneme detection list` — one declined path per line (corrupt ledger lines skipped silently). `_registration_nudge` returns `""` when the detected repo's resolved path is in the ledger, so a declined repo is NEVER nudged again — across sessions and compactions. The nudge's closing line changes from the instruction-only wording to: `If the user declines, run: mneme detection decline --cwd <path> — they will not be asked again for this repo.` The Plan 08 audit noted decline handling was best-effort instruction text; this persists it.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_detection_declines.py`:

```python
from mneme_core import paths
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_kb(tmp_path, name="declined-kb"):
    kb = tmp_path / name
    kb.mkdir()
    (kb / "MNEME.md").write_text("# scope\n", encoding="utf-8")
    return kb


def test_decline_suppresses_nudge_persistently(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert "Unregistered knowledge repo detected" in out
    code, out, _ = run(capsys, "--home", str(home), "detection", "decline", "--cwd", str(kb))
    assert code == 0
    assert "declined" in out
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert code == 0
    assert "Unregistered knowledge repo detected" not in out
    assert "mneme noticing" in out  # brief itself unaffected


def test_decline_is_idempotent_and_listed(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    run(capsys, "--home", str(home), "detection", "decline", "--cwd", str(kb))
    run(capsys, "--home", str(home), "detection", "decline", "--cwd", str(kb))
    code, out, _ = run(capsys, "--home", str(home), "detection", "list")
    assert code == 0
    assert out.strip().splitlines().count(str(kb.resolve())) == 1


def test_decline_outside_kb_fails(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    code, _, err = run(
        capsys, "--home", str(tmp_path / "h"), "detection", "decline", "--cwd", str(plain)
    )
    assert code == 1
    assert "mneme:" in err


def test_corrupt_ledger_lines_skipped(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    paths.ensure_layout(home)
    with paths.detection_declined_path(home).open("a", encoding="utf-8") as f:
        f.write("{corrupt\n")
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert code == 0
    assert "Unregistered knowledge repo detected" in out  # corrupt line neither crashes nor suppresses
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_detection_declines.py -v` — FAIL: no `detection` subcommand / no `detection_declined_path`.

- [ ] **Step 3: Implement**

Add `detection_declined_path` to `paths.py`. In `cli.py`: `detection` subparser group (`decline` and `list`, each `--cwd` where applicable); handlers append/read the JSONL (per-line try/except on parse); `_registration_nudge` gains an early return when `str(kb)` matches a ledger entry, and its closing line is updated per the Interfaces text (update the Plan 08 nudge tests ONLY if they pinned the old closing wording — record it as a deviation if so).

- [ ] **Step 4–5: Task tests green, full suite green, commit**

```bash
git add core/mneme_core/paths.py core/mneme_core/cli.py tests/core/test_detection_declines.py
git commit -m "feat: persisted detection declines"
```

---

### Task 7: Docs, spec, release 0.5.0

**Files:**
- Modify: `docs/superpowers/specs/2026-08-11-mneme-design.md` (facts location in §5.1/§5.3 with a dated revision note; new §7.7 Classify describing rails + prompt-driven triage + PR-only delivery), `README.md` (`/mneme:classify` row in the command table; knowledge-plugin tree shows `skills/knowledge-index/facts/`), `docs/install.md` (one paragraph), `CHANGELOG.md` (`## 0.5.0 — 2026-08-12`: facts under knowledge-index with legacy compat; prompt-driven classify with PR-only delivery and never-delete guarantee), version `0.5.0` in all four locations + test pin.
- Steps: pin test first → apply → full suite green → `bin/mneme lint .` and `claude plugin validate . --strict` exit 0 → commit `release: 0.5.0`.

---

## Verification (end of plan)

1. `python3 -m pytest -v` green; `bin/mneme lint .` exit 0; `claude plugin validate . --strict` exit 0; `bin/mneme --version` → `0.5.0`.
2. Live-shaped classify demo from a scratch home: scaffold, harvest one fact via `distill ingest` + `share apply --no-push`, merge the harvest branch manually (simulating an accepted PR), `mneme classify begin/prepare` (bundle lists the fact), scripted integrate + finalize `--no-push`, confirm classify branch exists, `main` untouched, knowledge-index regenerated without the moved fact.
3. Legacy-migration demo: repo with top-level `facts/` → after finalize, `git ls-tree -r --name-only <branch>` shows every fact under `skills/knowledge-index/facts/` and none under `facts/`.
4. One commit per task (7 new commits).

## Out of scope

- Automatic classification without user approval of the mapping (explicitly rejected — prompt-driven WITH a human gate).
- Cross-plugin fact moves (classify operates within one registered plugin).
- Headless/background classify (it is an interactive session activity by design).
