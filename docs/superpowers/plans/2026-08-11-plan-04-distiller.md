# Mneme Plan 04 — Routing + Distiller Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic half of the capture→distill pipeline (spec §7.1–7.2, §4.3): scope-driven routing support, the session noticing brief (`mneme context`), canonical unit composition from structured proposals, and the two-phase distiller CLI — `mneme distill prepare` (prompt bundle out) and `mneme distill ingest` (proposals in → machine gate → staged candidates).

**Architecture:** The distiller is split so the LLM never touches unaudited paths. `prepare` assembles everything the distiller agent needs (registry scopes = routing prompt, session flags, curation rubric, the proposal JSON schema). The harness adapter (Plan 06) pipes that through a headless agent. `ingest` accepts the agent's **structured proposals** and does everything else in tested code: validation, canonical rendering (`compose.py` builds spec-valid SKILL.md and fact bullets — format correctness by construction), secret-scan quarantine, declined-ledger and duplicate checks, index-similarity annotation, sensitivity-boundary flags, staging writes. LLM judgment lives in one reviewed prompt template; every gate is deterministic.

**Tech Stack:** Python ≥3.10 stdlib. Dev-only: `pytest`. No new dependencies.

**Spec:** §4.3 (routing, MNEME.md scope-as-routing-prompt, sensitivity boundaries), §5.2–5.3 (unit formats), §7.1 (noticing/flags/exclusions), §7.2 (distill triggers + machine gate), §8 (quarantine, boundaries). Builds on Plans 01–03 (`staging`, `scan`, `units`, `registry`, `templates`, `mneme_index`, scaffold's MNEME.md format).

## Global Constraints

- All Plan 01–03 Global Constraints still apply (stdlib-only, UTF-8, exit codes 0/1/2, strict TDD, kebab-case enums, import boundary, existing suite stays green).
- Several tasks modify files earlier plans shaped (`cli.py`, `staging.py`, `templates.py`) — always READ the current file and integrate; plan snippets show the delta, not the whole file.
- Rendered units must be valid by construction: every `compose` output must pass the corresponding lint (`lint_skill` / `parse_bullet_line`) — enforced by tests.
- Proposals are DATA, never code: `ingest` must treat every field as untrusted (validate enums, lengths, kebab names; scan every rendered body; single-line-fold fact text).
- Timestamps: ISO dates (`YYYY-MM-DD`) for `captured`/`verified` fields, from `datetime.now(timezone.utc)`.
- Run tests with `python3 -m pytest` from the repo root.

## File Structure

```
core/mneme_core/
├── staging.py     # Task 1: Candidate gains topic / similar_to / boundary_warning
├── routing.py     # Tasks 2–3 (new): scopes, sensitivity ranks, plugin_for_path
├── templates.py   # Task 4: NOTICING_BRIEF, DISTILLER_PROMPT
├── compose.py     # Task 5 (new): render_skill_unit, render_fact_bullet
├── proposals.py   # Task 6 (new): parse/validate proposal JSON
└── cli.py         # Task 7 (context), Tasks 8–10 (distill prepare/ingest)
tests/core/
├── test_staging_metadata.py   # Task 1
├── test_routing_scopes.py     # Task 2
├── test_routing_boundaries.py # Task 3
├── test_distiller_templates.py# Task 4
├── test_compose.py            # Task 5
├── test_proposals.py          # Task 6
├── test_cli_context.py        # Task 7
├── test_distill_prepare.py    # Task 8
├── test_distill_ingest.py     # Task 9
└── test_distill_ingest_annotations.py  # Task 10
```

---

### Task 1: Candidate metadata fields (`staging.py`)

**Files:**
- Modify: `core/mneme_core/staging.py`
- Create: `tests/core/test_staging_metadata.py`

**Interfaces:**
- Consumes: existing `Candidate`, `_to_text`, `_from_text`.
- Produces: `Candidate` gains three optional string fields, all defaulting to `""`, round-tripped through the frontmatter exactly like `target_unit`: `topic` (fact candidates: which `facts/<topic>.md` file the bullet belongs to; frontmatter key `topic`), `similar_to` (unit id of the nearest existing knowledge, annotated by ingest; key `similar-to`), `boundary_warning` (human-readable message when routing crosses a sensitivity boundary; key `boundary-warning`). Existing candidate files without these keys load with defaults — backward compatible.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_staging_metadata.py`:

```python
from mneme_core import staging
from mneme_core.staging import Candidate, candidate_id


def make(**kw):
    body = kw.pop("body", "- [gotcha] Fact text #x (verified: 2026-08-11)\n")
    defaults = dict(
        id=candidate_id("fact", "acme-knowledge", body),
        type="fact",
        edit="new",
        target="acme-knowledge",
        body=body,
    )
    defaults.update(kw)
    return Candidate(**defaults)


def test_new_fields_default_empty():
    cand = make()
    assert cand.topic == ""
    assert cand.similar_to == ""
    assert cand.boundary_warning == ""


def test_round_trip_with_metadata(tmp_path):
    cand = make(
        topic="staging-env",
        similar_to="facts/staging-env#staging-db-resets-nightly-at-04",
        boundary_warning="target 'public-kb' is public but source 'acme-knowledge' is internal",
    )
    staging.write_candidate(tmp_path, cand)
    loaded = staging.load_candidates(tmp_path)[0]
    assert loaded == cand


def test_legacy_candidate_without_new_keys_loads(tmp_path):
    cand = make()
    path = staging.write_candidate(tmp_path, cand)
    text = path.read_text(encoding="utf-8")
    stripped = "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith(("topic:", "similar-to:", "boundary-warning:"))
    ) + "\n"
    path.write_text(stripped, encoding="utf-8")
    loaded = staging.load_candidates(tmp_path)[0]
    assert loaded.topic == ""
    assert loaded.similar_to == ""
    assert loaded.boundary_warning == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_staging_metadata.py -v`
Expected: FAIL — `TypeError: Candidate.__init__() got an unexpected keyword argument 'topic'`.

- [ ] **Step 3: Modify `core/mneme_core/staging.py`**

In the `Candidate` dataclass, after `target_unit: str = ""`, add:

```python
    topic: str = ""
    similar_to: str = ""
    boundary_warning: str = ""
```

In `_to_text`, extend the meta dict (after the `"target-unit"` entry):

```python
        "topic": cand.topic,
        "similar-to": cand.similar_to,
        "boundary-warning": cand.boundary_warning,
```

In `_from_text`, extend the constructor call correspondingly:

```python
        topic=str(meta.get("topic", "")),
        similar_to=str(meta.get("similar-to", "")),
        boundary_warning=str(meta.get("boundary-warning", "")),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_staging_metadata.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/staging.py tests/core/test_staging_metadata.py
git commit -m "feat: candidate metadata — topic, similar-to, boundary-warning"
```

---

### Task 2: Scope extraction (`routing.py`, part 1)

**Files:**
- Create: `core/mneme_core/routing.py`, `tests/core/test_routing_scopes.py`

**Interfaces:**
- Consumes: `registry.load_registry`, `MnemeError`.
- Produces: `@dataclass Scope(name: str, sensitivity: str, mode: str, path: str, statement: str)`; `read_scope_statement(mneme_md: Path) -> str` — returns the text under the `## Scope statement` heading (up to the next `##` heading, stripped); `""` when the file or the heading is missing (never raises — routing must tolerate imperfect knowledge repos); `scopes(home: Path) -> list[Scope]` — one entry per registered plugin, statement read from `<plugin.path>/MNEME.md`, ordered by plugin name.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_routing_scopes.py`:

```python
from pathlib import Path

from mneme_core import registry, routing
from mneme_core.registry import Plugin

MNEME_MD = """# acme-knowledge — knowledge scope

**Sensitivity:** internal

## Scope statement

Widget platform operations: deploys, the staging environment, and the v2 API.

## What belongs here

- stuff
"""


def make_plugin_dir(root: Path, text: str = MNEME_MD) -> Path:
    root.mkdir(parents=True)
    (root / "MNEME.md").write_text(text, encoding="utf-8")
    return root


def test_read_scope_statement_extracts_section(tmp_path):
    d = make_plugin_dir(tmp_path / "kb")
    statement = routing.read_scope_statement(d / "MNEME.md")
    assert statement == "Widget platform operations: deploys, the staging environment, and the v2 API."


def test_read_scope_statement_missing_file_or_heading(tmp_path):
    assert routing.read_scope_statement(tmp_path / "absent" / "MNEME.md") == ""
    p = tmp_path / "noheading.md"
    p.write_text("# nothing here\n", encoding="utf-8")
    assert routing.read_scope_statement(p) == ""


def test_scopes_lists_registered_plugins_sorted(tmp_path):
    home = tmp_path / "home"
    b = make_plugin_dir(tmp_path / "b-kb")
    a = make_plugin_dir(tmp_path / "a-kb")
    registry.add_plugin(home, Plugin(name="b-plugin", repo="r", path=str(b), sensitivity="restricted"))
    registry.add_plugin(home, Plugin(name="a-plugin", repo="r", path=str(a)))
    result = routing.scopes(home)
    assert [s.name for s in result] == ["a-plugin", "b-plugin"]
    assert result[0].statement.startswith("Widget platform operations")
    assert result[1].sensitivity == "restricted"
    assert result[0].mode == "pr"


def test_scopes_tolerates_missing_clone(tmp_path):
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="gone-plugin", repo="r", path=str(tmp_path / "nope")))
    result = routing.scopes(home)
    assert result[0].statement == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_routing_scopes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.routing'`.

- [ ] **Step 3: Implement `core/mneme_core/routing.py`**

```python
"""Routing support: registered scopes and sensitivity boundaries (spec §4.3)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import registry


@dataclass
class Scope:
    name: str
    sensitivity: str
    mode: str
    path: str
    statement: str


def read_scope_statement(mneme_md: Path) -> str:
    try:
        text = mneme_md.read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    lines = text.splitlines()
    collected: list[str] = []
    in_section = False
    for line in lines:
        if line.strip().lower() == "## scope statement":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            collected.append(line)
    return "\n".join(collected).strip()


def scopes(home: Path) -> list[Scope]:
    out: list[Scope] = []
    for p in sorted(registry.load_registry(home), key=lambda pl: pl.name):
        statement = read_scope_statement(Path(p.path) / "MNEME.md")
        out.append(
            Scope(
                name=p.name,
                sensitivity=p.sensitivity,
                mode=p.mode,
                path=p.path,
                statement=statement,
            )
        )
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_routing_scopes.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/routing.py tests/core/test_routing_scopes.py
git commit -m "feat: routing scopes from registered MNEME.md files"
```

---

### Task 3: Sensitivity boundaries + path ownership (`routing.py`, part 2)

**Files:**
- Modify: `core/mneme_core/routing.py` (append)
- Create: `tests/core/test_routing_boundaries.py`

**Interfaces:**
- Consumes: Task 2's module.
- Produces: `SENSITIVITY_RANK = {"public": 0, "internal": 1, "restricted": 2}`; `boundary_warning(source_sensitivity: str, target: Scope) -> str` — non-empty human-readable message iff the target is LESS restricted than the source (`rank(target) < rank(source)`), e.g. `"target 'pub-kb' is public but the source context is restricted"`; unknown sensitivities rank as `internal`; `plugin_for_path(home: Path, cwd: Path) -> Scope | None` — the registered scope whose `path` contains `cwd` (deepest match wins; `None` when outside every registered plugin).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_routing_boundaries.py`:

```python
from pathlib import Path

from mneme_core import registry, routing
from mneme_core.registry import Plugin
from mneme_core.routing import Scope


def scope(name="t", sensitivity="internal", path="/x"):
    return Scope(name=name, sensitivity=sensitivity, mode="pr", path=path, statement="")


def test_less_restricted_target_warns():
    msg = routing.boundary_warning("restricted", scope(name="pub-kb", sensitivity="public"))
    assert "pub-kb" in msg and "public" in msg and "restricted" in msg


def test_equal_or_more_restricted_is_silent():
    assert routing.boundary_warning("internal", scope(sensitivity="internal")) == ""
    assert routing.boundary_warning("internal", scope(sensitivity="restricted")) == ""
    assert routing.boundary_warning("public", scope(sensitivity="internal")) == ""


def test_unknown_sensitivity_ranks_internal():
    assert routing.boundary_warning("wat", scope(sensitivity="internal")) == ""
    assert routing.boundary_warning("restricted", scope(sensitivity="wat")) != ""


def test_plugin_for_path_deepest_match(tmp_path):
    home = tmp_path / "home"
    outer = tmp_path / "repos" / "outer"
    inner = outer / "nested" / "inner"
    inner.mkdir(parents=True)
    registry.add_plugin(home, Plugin(name="outer-kb", repo="r", path=str(outer)))
    registry.add_plugin(home, Plugin(name="inner-kb", repo="r", path=str(inner)))
    hit = routing.plugin_for_path(home, inner / "facts")
    assert hit is not None and hit.name == "inner-kb"
    hit = routing.plugin_for_path(home, outer / "skills")
    assert hit is not None and hit.name == "outer-kb"
    assert routing.plugin_for_path(home, tmp_path / "elsewhere") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_routing_boundaries.py -v`
Expected: FAIL — `AttributeError: module 'mneme_core.routing' has no attribute 'boundary_warning'`.

- [ ] **Step 3: Append to `core/mneme_core/routing.py`**

```python
SENSITIVITY_RANK = {"public": 0, "internal": 1, "restricted": 2}


def _rank(sensitivity: str) -> int:
    return SENSITIVITY_RANK.get(sensitivity, SENSITIVITY_RANK["internal"])


def boundary_warning(source_sensitivity: str, target: Scope) -> str:
    if _rank(target.sensitivity) < _rank(source_sensitivity):
        return (
            f"target '{target.name}' is {target.sensitivity} but the source context"
            f" is {source_sensitivity}"
        )
    return ""


def plugin_for_path(home: Path, cwd: Path) -> Scope | None:
    best: Scope | None = None
    best_depth = -1
    cwd = cwd.resolve()
    for s in scopes(home):
        root = Path(s.path).resolve()
        if root == cwd or root in cwd.parents:
            depth = len(root.parts)
            if depth > best_depth:
                best, best_depth = s, depth
    return best
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_routing_boundaries.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/routing.py tests/core/test_routing_boundaries.py
git commit -m "feat: sensitivity boundaries and plugin-for-path routing"
```

---

### Task 4: Distiller + noticing templates (`templates.py`)

**Files:**
- Modify: `core/mneme_core/templates.py` (append)
- Create: `tests/core/test_distiller_templates.py`

**Interfaces:**
- Consumes: existing `render`.
- Produces: two constants. `NOTICING_BRIEF` (no placeholders) — the SessionStart instruction block: flag golden paths and knowledge issues in ONE line via `mneme flag`, never distill mid-session, respect exclusions. `DISTILLER_PROMPT` (`$scopes`, `$flags`, `$transcript_path`) — the distiller-agent prompt: separate-role framing; the promotion rule (verified success + named failure pattern + non-obvious); route by the scope statements; prefer `update` edits against existing units (it may run `bin/mneme search` / `bin/mneme db query` to check); emit ONLY a JSON object matching the proposal schema, which is embedded verbatim in the prompt (both `skill` and `fact` shapes with every field name ingest validates).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_distiller_templates.py`:

```python
from mneme_core import templates


def test_noticing_brief_contents():
    text = templates.NOTICING_BRIEF
    assert "mneme flag" in text
    assert "knowledge-issue" in text
    assert "one line" in text.lower()


def test_distiller_prompt_renders_and_carries_contract():
    text = templates.render(
        templates.DISTILLER_PROMPT,
        scopes="- acme-knowledge [internal/pr]: Widget platform operations.",
        flags='{"kind": "golden-path", "text": "solved the deploy race"}',
        transcript_path="/tmp/session.jsonl",
    )
    assert "acme-knowledge" in text
    assert "solved the deploy race" in text
    assert "/tmp/session.jsonl" in text
    for token in (
        '"proposals"', '"type"', '"edit"', '"target"', '"confidence"', '"rationale"',
        '"name"', '"description"', '"procedure"', '"failure_pattern"',
        '"topic"', '"category"', '"text"', '"tags"', '"target_unit"',
    ):
        assert token in text, token
    assert "failure pattern" in text.lower()
    assert "unassigned" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_distiller_templates.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'NOTICING_BRIEF'`.

- [ ] **Step 3: Append to `core/mneme_core/templates.py`**

```python
NOTICING_BRIEF = """## mneme noticing

While you work, flag knowledge worth keeping — do NOT stop to document it.

Flag (one line each, at the moment it happens) when:
- a hard-won fix lands after real dead ends: `mneme flag "<what worked + why it was non-obvious>"`
- installed knowledge proves wrong or stale: `mneme flag --kind knowledge-issue "<what is wrong>"`

Rules: one line per flag; no mid-session distillation (a background distiller runs later);
never flag anything from excluded repos/paths; never include secrets or credentials in flag text.
"""

DISTILLER_PROMPT = """You are the mneme DISTILLER — a separate curation role, not the working agent.
Read the session evidence and extract ONLY knowledge that clears the promotion rule:
1. Verified success — it actually worked in this session, not assumed.
2. A named failure pattern — what went wrong before the fix; dead ends eliminated.
3. Non-obvious — not derivable from public documentation.

Session flags (the working agent marked these moments):
$flags

Transcript: $transcript_path

Registered knowledge plugins (route each proposal to the best-matching scope;
use "unassigned" when no scope clearly fits — never guess across scopes):
$scopes

Before proposing, check what already exists: you may run `bin/mneme search "<query>"`
and `bin/mneme db query "SELECT ..."`. When existing knowledge covers the same ground,
emit an "update" edit against that unit id instead of a near-duplicate "new".

Output EXACTLY one JSON object, no prose, matching:
{
  "proposals": [
    {
      "type": "skill", "edit": "new" | "update", "target": "<plugin-name>" | "unassigned",
      "target_unit": "<unit id, required when edit=update>",
      "name": "<kebab-case-skill-name>", "description": "<trigger-rich, <=1024 chars>",
      "procedure": "<verified steps, markdown>", "failure_pattern": "<what failed first, markdown>",
      "confidence": 0.0, "rationale": "<why this clears the promotion rule>"
    },
    {
      "type": "fact", "edit": "new" | "update", "target": "<plugin-name>" | "unassigned",
      "target_unit": "<unit id, required when edit=update>",
      "topic": "<kebab-case-topic>", "category": "decision|constraint|gotcha|runbook-note|reference",
      "text": "<single factual statement>", "tags": ["<tag>"],
      "confidence": 0.0, "rationale": "<why this clears the promotion rule>"
    }
  ]
}

Emit an empty proposals array when nothing clears the rule — silence beats noise.
Never include secrets, tokens, passwords, or personal data in any field.
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_distiller_templates.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/templates.py tests/core/test_distiller_templates.py
git commit -m "feat: noticing brief and distiller prompt templates"
```

---

### Task 5: Canonical unit composition (`compose.py`)

**Files:**
- Create: `core/mneme_core/compose.py`, `tests/core/test_compose.py`

**Interfaces:**
- Consumes: `units.serialize_frontmatter`, `units.parse_bullet_line`, `units.KEBAB_RE`, `lint.lint_skill`, `MnemeError`.
- Produces: `render_skill_unit(name: str, description: str, procedure: str, failure_pattern: str, *, source: str, captured: str) -> str` — a complete SKILL.md text: frontmatter (`name`, `description`, `metadata` map with `mneme-type: skill`, `mneme-source`, `mneme-captured`, `mneme-last-verified` = captured) + body `# <name>\n\n## Procedure\n\n<procedure>\n\n## Failure pattern\n\n<failure_pattern>\n`; `render_fact_bullet(category: str, text: str, tags: list[str], *, verified: str) -> str` — a single `- [<category>] <text> <#tags> (verified: <date>)` line; multi-line/whitespace-heavy `text` is folded to single-spaced one line; the rendered line MUST round-trip through `units.parse_bullet_line` (raise `MnemeError` if it cannot, e.g. text that still breaks the grammar after folding).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_compose.py`:

```python
import pytest

from mneme_core import compose, lint, units
from mneme_core.errors import MnemeError


def test_skill_unit_is_lint_clean(tmp_path):
    text = compose.render_skill_unit(
        "deploy-widget",
        "Use when deploying the widget service after a failed cutover",
        "1. Run preflight.\n2. Cut over blue-green.",
        "Naive restart loops forever because the LB caches the dead target.",
        source="acme/app@session-42",
        captured="2026-08-11",
    )
    d = tmp_path / "skills" / "deploy-widget"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    assert lint.lint_skill(d) == []
    meta, body = units.parse_frontmatter(text)
    assert meta["name"] == "deploy-widget"
    assert meta["metadata"]["mneme-type"] == "skill"
    assert meta["metadata"]["mneme-captured"] == "2026-08-11"
    assert meta["metadata"]["mneme-last-verified"] == "2026-08-11"
    assert meta["metadata"]["mneme-source"] == "acme/app@session-42"
    assert "## Procedure" in body
    assert "## Failure pattern" in body
    assert "LB caches the dead target" in body


def test_fact_bullet_round_trips():
    line = compose.render_fact_bullet(
        "constraint",
        "Staging DB resets nightly at 04:00 UTC",
        ["staging", "db"],
        verified="2026-08-11",
    )
    b = units.parse_bullet_line(line, 1)
    assert b.category == "constraint"
    assert b.text == "Staging DB resets nightly at 04:00 UTC"
    assert b.tags == ["staging", "db"]
    assert b.verified == "2026-08-11"


def test_fact_bullet_folds_multiline_text():
    line = compose.render_fact_bullet(
        "gotcha", "line one\n   line two\t tabbed", [], verified="2026-08-11"
    )
    b = units.parse_bullet_line(line, 1)
    assert b.text == "line one line two tabbed"


def test_fact_bullet_no_tags_no_trailing_gap():
    line = compose.render_fact_bullet("reference", "See runbook", [], verified="2026-08-11")
    assert line == "- [reference] See runbook (verified: 2026-08-11)"


def test_invalid_inputs_raise():
    with pytest.raises(MnemeError):
        compose.render_skill_unit("Bad_Name", "d", "p", "f", source="s", captured="2026-08-11")
    with pytest.raises(MnemeError):
        compose.render_skill_unit("ok-name", "", "p", "f", source="s", captured="2026-08-11")
    with pytest.raises(MnemeError):
        compose.render_fact_bullet("bogus", "text", [], verified="2026-08-11")
    with pytest.raises(MnemeError):
        compose.render_fact_bullet("gotcha", "", [], verified="2026-08-11")
    with pytest.raises(MnemeError):
        compose.render_fact_bullet("gotcha", "text", ["bad tag!"], verified="2026-08-11")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_compose.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.compose'`.

- [ ] **Step 3: Implement `core/mneme_core/compose.py`**

```python
"""Canonical unit rendering — proposals in, spec-valid units out (spec §5.2–5.3)."""
from __future__ import annotations

import re

from . import units
from .errors import MnemeError

_TAG_RE = re.compile(r"^[\w-]+$")
_MAX_DESCRIPTION = 1024


def render_skill_unit(
    name: str,
    description: str,
    procedure: str,
    failure_pattern: str,
    *,
    source: str,
    captured: str,
) -> str:
    if not units.KEBAB_RE.match(name):
        raise MnemeError(f"skill name must be kebab-case: {name!r}")
    if not description.strip():
        raise MnemeError("skill description must not be empty")
    if len(description) > _MAX_DESCRIPTION:
        raise MnemeError(f"skill description exceeds {_MAX_DESCRIPTION} chars")
    if not procedure.strip():
        raise MnemeError("skill procedure must not be empty")
    if not failure_pattern.strip():
        raise MnemeError("skill failure_pattern must not be empty")
    meta = {
        "name": name,
        "description": description.strip(),
        "metadata": {
            "mneme-type": "skill",
            "mneme-source": source,
            "mneme-captured": captured,
            "mneme-last-verified": captured,
        },
    }
    body = (
        f"# {name}\n\n"
        f"## Procedure\n\n{procedure.strip()}\n\n"
        f"## Failure pattern\n\n{failure_pattern.strip()}\n"
    )
    return units.serialize_frontmatter(meta, body)


def render_fact_bullet(
    category: str, text: str, tags: list[str], *, verified: str
) -> str:
    if category not in units.FACT_CATEGORIES:
        raise MnemeError(f"unknown fact category: {category!r}")
    folded = " ".join(text.split())
    if not folded:
        raise MnemeError("fact text must not be empty")
    for tag in tags:
        if not _TAG_RE.match(tag):
            raise MnemeError(f"invalid tag: {tag!r}")
    tag_part = "".join(f" #{t}" for t in tags)
    line = f"- [{category}] {folded}{tag_part} (verified: {verified})"
    try:
        units.parse_bullet_line(line, 1)
    except MnemeError:
        raise MnemeError(f"fact text does not survive bullet grammar: {folded!r}")
    return line
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_compose.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/compose.py tests/core/test_compose.py
git commit -m "feat: canonical unit composition from structured fields"
```

---

### Task 6: Proposal validation (`proposals.py`)

**Files:**
- Create: `core/mneme_core/proposals.py`, `tests/core/test_proposals.py`

**Interfaces:**
- Consumes: `units.KEBAB_RE`, `units.FACT_CATEGORIES`, `staging.TYPES`, `staging.EDITS`, `MnemeError`.
- Produces: `@dataclass Proposal(type: str, edit: str, target: str, confidence: float, rationale: str, target_unit: str, name: str, description: str, procedure: str, failure_pattern: str, topic: str, category: str, text: str, tags: list[str])` (unused fields default `""`/`[]`); `parse_proposals(raw: str) -> tuple[list[Proposal], list[str]]` — parses the JSON document (`MnemeError` on non-JSON or missing top-level `proposals` list), validates each entry independently, returns `(valid, errors)` where each error string is `"proposal <index>: <reason>"`. Validation: `type` in TYPES; `edit` in EDITS; `edit=update` requires `target_unit`; `confidence` coercible to float in [0, 1] (default 0.5 when absent); skill entries require kebab `name`, non-empty `description` ≤1024, non-empty `procedure` and `failure_pattern`; fact entries require kebab `topic`, valid `category`, non-empty `text`, `tags` a list of `[\w-]+` strings; `target` defaults to `"unassigned"` when absent/empty. A malformed entry never blocks the rest.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_proposals.py`:

```python
import json

import pytest

from mneme_core import proposals
from mneme_core.errors import MnemeError


def skill_entry(**kw):
    entry = dict(
        type="skill", edit="new", target="acme-knowledge",
        name="deploy-widget", description="Use when deploying widgets",
        procedure="Steps.", failure_pattern="What failed first.",
        confidence=0.8, rationale="verified in session",
    )
    entry.update(kw)
    return entry


def fact_entry(**kw):
    entry = dict(
        type="fact", edit="new", target="acme-knowledge",
        topic="staging-env", category="constraint",
        text="DB resets nightly", tags=["staging"],
        confidence=0.7, rationale="observed twice",
    )
    entry.update(kw)
    return entry


def parse(entries):
    return proposals.parse_proposals(json.dumps({"proposals": entries}))


def test_valid_skill_and_fact():
    valid, errors = parse([skill_entry(), fact_entry()])
    assert errors == []
    assert [p.type for p in valid] == ["skill", "fact"]
    assert valid[0].name == "deploy-widget"
    assert valid[1].tags == ["staging"]


def test_bad_entries_reported_independently():
    valid, errors = parse(
        [
            skill_entry(name="Bad_Name"),
            fact_entry(),
            fact_entry(category="bogus"),
            skill_entry(edit="update"),  # missing target_unit
        ]
    )
    assert len(valid) == 1
    assert len(errors) == 3
    assert errors[0].startswith("proposal 0:")
    assert errors[1].startswith("proposal 2:")
    assert errors[2].startswith("proposal 3:")


def test_defaults_applied():
    valid, errors = parse([{k: v for k, v in fact_entry().items() if k not in ("target", "confidence")}])
    assert errors == []
    assert valid[0].target == "unassigned"
    assert valid[0].confidence == 0.5


def test_confidence_bounds():
    _, errors = parse([fact_entry(confidence=1.5)])
    assert len(errors) == 1
    _, errors = parse([fact_entry(confidence="not-a-number")])
    assert len(errors) == 1


def test_non_json_raises():
    with pytest.raises(MnemeError):
        proposals.parse_proposals("not json at all")
    with pytest.raises(MnemeError):
        proposals.parse_proposals(json.dumps({"nope": []}))


def test_update_with_target_unit_ok():
    valid, errors = parse([fact_entry(edit="update", target_unit="facts/staging-env#db-resets")])
    assert errors == []
    assert valid[0].target_unit == "facts/staging-env#db-resets"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_proposals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.proposals'`.

- [ ] **Step 3: Implement `core/mneme_core/proposals.py`**

```python
"""Distiller proposal parsing — untrusted structured data in, validated objects out."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import staging, units
from .errors import MnemeError

UNASSIGNED = staging.UNASSIGNED
_MAX_DESCRIPTION = 1024


@dataclass
class Proposal:
    type: str
    edit: str
    target: str
    confidence: float
    rationale: str
    target_unit: str = ""
    name: str = ""
    description: str = ""
    procedure: str = ""
    failure_pattern: str = ""
    topic: str = ""
    category: str = ""
    text: str = ""
    tags: list[str] = field(default_factory=list)


def _validate(entry: dict) -> Proposal:
    if not isinstance(entry, dict):
        raise MnemeError("entry is not an object")
    type_ = str(entry.get("type", ""))
    if type_ not in staging.TYPES:
        raise MnemeError(f"type must be one of {sorted(staging.TYPES)}: {type_!r}")
    edit = str(entry.get("edit", "new"))
    if edit not in staging.EDITS:
        raise MnemeError(f"edit must be one of {sorted(staging.EDITS)}: {edit!r}")
    target_unit = str(entry.get("target_unit", ""))
    if edit == "update" and not target_unit:
        raise MnemeError("update proposals must set target_unit")
    target = str(entry.get("target") or UNASSIGNED)
    raw_conf = entry.get("confidence", 0.5)
    try:
        confidence = float(raw_conf)
    except (TypeError, ValueError):
        raise MnemeError(f"confidence is not a number: {raw_conf!r}")
    if not 0.0 <= confidence <= 1.0:
        raise MnemeError(f"confidence out of range [0, 1]: {confidence}")
    rationale = str(entry.get("rationale", ""))

    p = Proposal(
        type=type_, edit=edit, target=target, confidence=confidence,
        rationale=rationale, target_unit=target_unit,
    )
    if type_ == "skill":
        p.name = str(entry.get("name", ""))
        p.description = str(entry.get("description", ""))
        p.procedure = str(entry.get("procedure", ""))
        p.failure_pattern = str(entry.get("failure_pattern", ""))
        if not units.KEBAB_RE.match(p.name):
            raise MnemeError(f"skill name must be kebab-case: {p.name!r}")
        if not p.description.strip():
            raise MnemeError("skill description must not be empty")
        if len(p.description) > _MAX_DESCRIPTION:
            raise MnemeError(f"skill description exceeds {_MAX_DESCRIPTION} chars")
        if not p.procedure.strip():
            raise MnemeError("skill procedure must not be empty")
        if not p.failure_pattern.strip():
            raise MnemeError("skill failure_pattern must not be empty")
    else:
        p.topic = str(entry.get("topic", ""))
        p.category = str(entry.get("category", ""))
        p.text = str(entry.get("text", ""))
        raw_tags = entry.get("tags", [])
        if not isinstance(raw_tags, list):
            raise MnemeError("tags must be a list")
        p.tags = [str(t) for t in raw_tags]
        if not units.KEBAB_RE.match(p.topic):
            raise MnemeError(f"fact topic must be kebab-case: {p.topic!r}")
        if p.category not in units.FACT_CATEGORIES:
            raise MnemeError(f"unknown fact category: {p.category!r}")
        if not p.text.strip():
            raise MnemeError("fact text must not be empty")
    return p


def parse_proposals(raw: str) -> tuple[list[Proposal], list[str]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise MnemeError(f"proposals are not valid JSON: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("proposals"), list):
        raise MnemeError("proposals document must be an object with a 'proposals' list")
    valid: list[Proposal] = []
    errors: list[str] = []
    for i, entry in enumerate(data["proposals"]):
        try:
            valid.append(_validate(entry))
        except MnemeError as e:
            errors.append(f"proposal {i}: {e}")
    return valid, errors
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_proposals.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/proposals.py tests/core/test_proposals.py
git commit -m "feat: distiller proposal parsing and validation"
```

---

### Task 7: `mneme context` command

**Files:**
- Modify: `core/mneme_core/cli.py`
- Create: `tests/core/test_cli_context.py`

**Interfaces:**
- Consumes: `templates.NOTICING_BRIEF`, `routing.scopes`.
- Produces: `mneme context` — prints the noticing brief followed by a `Registered knowledge plugins:` section, one line per scope: `- <name> [<sensitivity>/<mode>]: <first line of scope statement>` (or `- <name> [...]: (no scope statement)` when empty). With no registered plugins, prints the brief plus `Registered knowledge plugins: none — run 'mneme new <name>' to create one.` Exit 0 always (this feeds a SessionStart hook; it must never fail the session).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_cli_context.py`:

```python
from mneme_core import registry
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_context_with_plugins(tmp_path, capsys):
    home = tmp_path / "home"
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "MNEME.md").write_text(
        "# x\n\n## Scope statement\n\nWidget platform operations.\nMore detail.\n",
        encoding="utf-8",
    )
    registry.add_plugin(
        home, Plugin(name="acme-knowledge", repo="r", path=str(kb), sensitivity="restricted")
    )
    code, out, _ = run(capsys, "--home", str(home), "context")
    assert code == 0
    assert "mneme flag" in out
    assert "- acme-knowledge [restricted/pr]: Widget platform operations." in out
    assert "More detail." not in out


def test_context_without_plugins(tmp_path, capsys):
    code, out, _ = run(capsys, "--home", str(tmp_path / "home"), "context")
    assert code == 0
    assert "none — run 'mneme new" in out


def test_context_missing_scope_statement(tmp_path, capsys):
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="bare-kb", repo="r", path=str(tmp_path / "nope")))
    code, out, _ = run(capsys, "--home", str(home), "context")
    assert code == 0
    assert "- bare-kb [internal/pr]: (no scope statement)" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_cli_context.py -v`
Expected: FAIL — argparse `invalid choice: 'context'`.

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

In `_build_parser`: `sub.add_parser("context")`. In the dispatch:

```python
        if args.command == "context":
            from . import routing, templates

            print(templates.NOTICING_BRIEF)
            scope_list = routing.scopes(home)
            if not scope_list:
                print("Registered knowledge plugins: none — run 'mneme new <name>' to create one.")
                return 0
            print("Registered knowledge plugins:")
            for s in scope_list:
                first = s.statement.splitlines()[0] if s.statement else "(no scope statement)"
                print(f"- {s.name} [{s.sensitivity}/{s.mode}]: {first}")
            return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_cli_context.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_cli_context.py
git commit -m "feat: mneme context — session noticing brief"
```

---

### Task 8: `mneme distill prepare`

**Files:**
- Modify: `core/mneme_core/cli.py`
- Create: `tests/core/test_distill_prepare.py`

**Interfaces:**
- Consumes: `templates.DISTILLER_PROMPT`, `templates.render`, `routing.scopes`, `flags.read_flags`.
- Produces: `mneme distill prepare [--transcript PATH]` — prints a JSON object `{"prompt": <rendered DISTILLER_PROMPT>, "flag_count": N}`. The prompt's `$scopes` renders as one line per registered scope (`- <name> [<sensitivity>/<mode>]: <full statement, newlines folded to spaces>`, or `- (none registered)`); `$flags` renders as one JSON line per flag record (or `(no flags this session)`); `$transcript_path` from `--transcript` (default `(not provided)`). Exit 0; with zero flags it still emits (the adapter decides whether to run the distiller).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_distill_prepare.py`:

```python
import json

from mneme_core import flags, registry
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_prepare_bundles_scopes_and_flags(tmp_path, capsys):
    home = tmp_path / "home"
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "MNEME.md").write_text(
        "# x\n\n## Scope statement\n\nWidget ops.\nSecond line.\n", encoding="utf-8"
    )
    registry.add_plugin(home, Plugin(name="acme-knowledge", repo="r", path=str(kb)))
    flags.add_flag(home, "solved the deploy race", session="s1")
    code, out, _ = run(
        capsys, "--home", str(home), "distill", "prepare", "--transcript", "/tmp/t.jsonl"
    )
    assert code == 0
    bundle = json.loads(out)
    assert bundle["flag_count"] == 1
    prompt = bundle["prompt"]
    assert "- acme-knowledge [internal/pr]: Widget ops. Second line." in prompt
    assert "solved the deploy race" in prompt
    assert "/tmp/t.jsonl" in prompt
    assert '"proposals"' in prompt


def test_prepare_empty_home(tmp_path, capsys):
    code, out, _ = run(capsys, "--home", str(tmp_path / "home"), "distill", "prepare")
    assert code == 0
    bundle = json.loads(out)
    assert bundle["flag_count"] == 0
    assert "(none registered)" in bundle["prompt"]
    assert "(no flags this session)" in bundle["prompt"]
    assert "(not provided)" in bundle["prompt"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_distill_prepare.py -v`
Expected: FAIL — argparse `invalid choice: 'distill'`.

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

In `_build_parser`:

```python
    p_distill = sub.add_parser("distill")
    distill_sub = p_distill.add_subparsers(dest="distill_command", required=True)
    p_prep = distill_sub.add_parser("prepare")
    p_prep.add_argument("--transcript", default="(not provided)")
```

Dispatch:

```python
        if args.command == "distill":
            return _distill_cmd(home, args)
```

Handler (module bottom):

```python
def _distill_cmd(home: Path, args: argparse.Namespace) -> int:
    if args.distill_command == "prepare":
        import json as json_mod

        from . import flags as flags_mod
        from . import routing, templates

        scope_list = routing.scopes(home)
        if scope_list:
            scope_lines = "\n".join(
                f"- {s.name} [{s.sensitivity}/{s.mode}]: {' '.join(s.statement.split()) or '(no scope statement)'}"
                for s in scope_list
            )
        else:
            scope_lines = "- (none registered)"
        flag_records = flags_mod.read_flags(home)
        flag_lines = (
            "\n".join(json_mod.dumps(f) for f in flag_records)
            if flag_records
            else "(no flags this session)"
        )
        prompt = templates.render(
            templates.DISTILLER_PROMPT,
            scopes=scope_lines,
            flags=flag_lines,
            transcript_path=args.transcript,
        )
        print(json_mod.dumps({"prompt": prompt, "flag_count": len(flag_records)}))
        return 0
    return 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_distill_prepare.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_distill_prepare.py
git commit -m "feat: mneme distill prepare — distiller prompt bundle"
```

---

### Task 9: `mneme distill ingest` — the machine gate

**Files:**
- Modify: `core/mneme_core/cli.py`
- Create: `tests/core/test_distill_ingest.py`

**Interfaces:**
- Consumes: `proposals.parse_proposals`, `compose.render_skill_unit`, `compose.render_fact_bullet`, `scan.scan_text`, `scan.has_blockers`, `staging` (all of it), `flags.clear_flags`.
- Produces: `mneme distill ingest PATH|- [--source LABEL] [--clear-flags]` — reads the proposals JSON, then per valid proposal: compose the canonical body (skills → full SKILL.md via `render_skill_unit` with `source` = `--source` (default `"unknown"`) and `captured` = today UTC; facts → bullet line via `render_fact_bullet` with `verified` = today); compose failures count as rejected (reported like validation errors); **declined check** (`staging.is_declined(home, body)`) → skipped; **duplicate check** (`candidate_id` already staged/quarantined) → skipped; **scan** (`has_blockers` on the body) → candidate written with `status="quarantined"`; otherwise staged. Candidate fields: id, type, edit, target, target_unit, topic (facts), confidence, rationale, provenance `{source, captured}`. Prints a summary: `staged N  quarantined N  skipped-declined N  skipped-duplicate N  rejected N`, plus one `rejected: proposal i: reason` line each. `--clear-flags` clears the session flags after a successful run. Exit 0 even when everything was rejected (an empty distillation is not an error); exit 1 only on unreadable input/invalid JSON document.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_distill_ingest.py`:

```python
import json

from mneme_core import flags, staging
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def skill_entry(**kw):
    entry = dict(
        type="skill", edit="new", target="acme-knowledge",
        name="deploy-widget", description="Use when deploying widgets",
        procedure="Steps.", failure_pattern="What failed first.",
        confidence=0.8, rationale="verified in session",
    )
    entry.update(kw)
    return entry


def fact_entry(**kw):
    entry = dict(
        type="fact", edit="new", target="acme-knowledge",
        topic="staging-env", category="constraint",
        text="DB resets nightly", tags=["staging"],
        confidence=0.7, rationale="observed twice",
    )
    entry.update(kw)
    return entry


def write_proposals(tmp_path, entries):
    p = tmp_path / "proposals.json"
    p.write_text(json.dumps({"proposals": entries}), encoding="utf-8")
    return str(p)


def test_ingest_stages_valid_proposals(tmp_path, capsys):
    home = tmp_path / "home"
    path = write_proposals(tmp_path, [skill_entry(), fact_entry()])
    code, out, _ = run(
        capsys, "--home", str(home), "distill", "ingest", path, "--source", "repo@s1"
    )
    assert code == 0
    assert "staged 2" in out
    cands = staging.load_candidates(home)
    assert len(cands) == 2
    skill = next(c for c in cands if c.type == "skill")
    assert "## Failure pattern" in skill.body
    assert skill.provenance["source"] == "repo@s1"
    fact = next(c for c in cands if c.type == "fact")
    assert fact.topic == "staging-env"
    assert fact.body.startswith("- [constraint] DB resets nightly #staging")


def test_ingest_quarantines_secrets(tmp_path, capsys):
    home = tmp_path / "home"
    path = write_proposals(
        tmp_path, [fact_entry(text="The staging key is AKIAIOSFODNN7EXAMPLE")]
    )
    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert "quarantined 1" in out
    assert staging.load_candidates(home) == []
    q = staging.load_candidates(home, include_quarantined=True)
    assert len(q) == 1
    assert q[0].status == "quarantined"


def test_ingest_respects_declined_ledger(tmp_path, capsys):
    home = tmp_path / "home"
    path = write_proposals(tmp_path, [fact_entry()])
    run(capsys, "--home", str(home), "distill", "ingest", path)
    cand = staging.load_candidates(home)[0]
    staging.decline(home, cand, "not useful")
    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert "skipped-declined 1" in out
    assert staging.load_candidates(home) == []


def test_ingest_skips_duplicates(tmp_path, capsys):
    home = tmp_path / "home"
    path = write_proposals(tmp_path, [fact_entry()])
    run(capsys, "--home", str(home), "distill", "ingest", path)
    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert "skipped-duplicate 1" in out
    assert len(staging.load_candidates(home)) == 1


def test_ingest_reports_rejected(tmp_path, capsys):
    home = tmp_path / "home"
    path = write_proposals(tmp_path, [skill_entry(name="Bad_Name"), fact_entry()])
    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert "rejected 1" in out
    assert "rejected: proposal 0:" in out
    assert len(staging.load_candidates(home)) == 1


def test_ingest_clear_flags(tmp_path, capsys):
    home = tmp_path / "home"
    flags.add_flag(home, "something")
    path = write_proposals(tmp_path, [fact_entry()])
    run(capsys, "--home", str(home), "distill", "ingest", path, "--clear-flags")
    assert flags.read_flags(home) == []


def test_ingest_bad_document_exits_1(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    code, _, err = run(capsys, "--home", str(tmp_path / "h"), "distill", "ingest", str(bad))
    assert code == 1
    assert "mneme:" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_distill_ingest.py -v`
Expected: FAIL — argparse `invalid choice: 'ingest'`.

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

In `_build_parser`, inside the `distill` group:

```python
    p_ing = distill_sub.add_parser("ingest")
    p_ing.add_argument("path")
    p_ing.add_argument("--source", default="unknown")
    p_ing.add_argument("--clear-flags", action="store_true")
```

Extend `_distill_cmd` with the ingest branch:

```python
    if args.distill_command == "ingest":
        return _distill_ingest(home, args)
```

New handler:

```python
def _distill_ingest(home: Path, args: argparse.Namespace) -> int:
    import sys as sys_mod
    from datetime import datetime, timezone

    from . import compose, proposals as proposals_mod, scan as scan_mod, staging as staging_mod
    from . import flags as flags_mod

    if args.path == "-":
        raw = sys_mod.stdin.read()
    else:
        try:
            raw = Path(args.path).read_text(encoding="utf-8")
        except OSError as e:
            raise MnemeError(f"cannot read proposals: {e}")
    valid, errors = proposals_mod.parse_proposals(raw)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    staged = quarantined = skipped_declined = skipped_duplicate = 0
    rejected = list(errors)
    existing_ids = {
        c.id for c in staging_mod.load_candidates(home, include_quarantined=True)
    }
    for p in valid:
        try:
            if p.type == "skill":
                body = compose.render_skill_unit(
                    p.name, p.description, p.procedure, p.failure_pattern,
                    source=args.source, captured=today,
                )
            else:
                body = compose.render_fact_bullet(
                    p.category, p.text, p.tags, verified=today
                )
        except MnemeError as e:
            rejected.append(f"compose ({p.type} -> {p.target}): {e}")
            continue
        if staging_mod.is_declined(home, body):
            skipped_declined += 1
            continue
        cand_id = staging_mod.candidate_id(p.type, p.target, body)
        if cand_id in existing_ids:
            skipped_duplicate += 1
            continue
        findings = scan_mod.scan_text(body)
        status = "quarantined" if scan_mod.has_blockers(findings) else "staged"
        cand = staging_mod.Candidate(
            id=cand_id, type=p.type, edit=p.edit, target=p.target, body=body,
            confidence=p.confidence, rationale=p.rationale, target_unit=p.target_unit,
            topic=p.topic, status=status,
            provenance={"source": args.source, "captured": today},
        )
        staging_mod.write_candidate(home, cand)
        existing_ids.add(cand_id)
        if status == "quarantined":
            quarantined += 1
        else:
            staged += 1

    print(
        f"staged {staged}  quarantined {quarantined}"
        f"  skipped-declined {skipped_declined}"
        f"  skipped-duplicate {skipped_duplicate}  rejected {len(rejected)}"
    )
    for r in rejected:
        print(f"rejected: {r}")
    if args.clear_flags:
        flags_mod.clear_flags(home)
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_distill_ingest.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_distill_ingest.py
git commit -m "feat: mneme distill ingest — deterministic machine gate"
```

---

### Task 10: Ingest annotations — similarity + sensitivity boundaries

**Files:**
- Modify: `core/mneme_core/cli.py` (`_distill_ingest`)
- Create: `tests/core/test_distill_ingest_annotations.py`

**Interfaces:**
- Consumes: `mneme_index.db.open_db_readonly`, `mneme_index.search.search`, `routing.scopes`, `routing.boundary_warning`, `paths.db_path`.
- Produces: two annotations on candidates as they are written (never blocking):
  - **similar_to**: when the index DB exists, query it (`search(conn, <description or text>, k=1)`) and record the top hit's unit id in `Candidate.similar_to`; a proposal whose exact unit id equals the top hit still stages (the human decides at harvest). DB absent or query failure → annotation silently skipped (the DB never blocks, spec §6).
  - **boundary_warning**: new `--source-plugin NAME` option; when given and the target is a registered scope with lower sensitivity rank than the source plugin's, `Candidate.boundary_warning` gets `routing.boundary_warning(...)`'s message. Unregistered targets and `unassigned` never warn. Summary line gains `  boundary-warnings N` at the end.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_distill_ingest_annotations.py`:

```python
import json

from mneme_core import registry, staging
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def fact_entry(**kw):
    entry = dict(
        type="fact", edit="new", target="acme-knowledge",
        topic="staging-env", category="constraint",
        text="Staging DB resets nightly at 04:00 UTC", tags=["staging"],
        confidence=0.7, rationale="observed twice",
    )
    entry.update(kw)
    return entry


def write_proposals(tmp_path, entries):
    p = tmp_path / "proposals.json"
    p.write_text(json.dumps({"proposals": entries}), encoding="utf-8")
    return str(p)


def make_kb(tmp_path, name="acme-knowledge", sensitivity="internal"):
    kb = tmp_path / name
    facts = kb / "facts"
    facts.mkdir(parents=True)
    (facts / "staging-env.md").write_text(
        "---\ntopic: staging-env\n---\n"
        "- [constraint] Staging DB resets nightly at 04:00 UTC #staging (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    (kb / "MNEME.md").write_text(
        f"# {name}\n\n## Scope statement\n\nWidget ops.\n", encoding="utf-8"
    )
    return kb


def test_similar_to_annotated_when_index_exists(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    registry.add_plugin(home, Plugin(name="acme-knowledge", repo="r", path=str(kb)))
    run(capsys, "--home", str(home), "index", "rebuild")
    path = write_proposals(tmp_path, [fact_entry(text="The staging DB resets nightly around 04:00")])
    code, _, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    cand = staging.load_candidates(home)[0]
    assert cand.similar_to == "facts/staging-env#staging-db-resets-nightly-at-04"


def test_no_index_no_annotation_no_failure(tmp_path, capsys):
    home = tmp_path / "home"
    path = write_proposals(tmp_path, [fact_entry()])
    code, _, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert staging.load_candidates(home)[0].similar_to == ""


def test_boundary_warning_on_less_restricted_target(tmp_path, capsys):
    home = tmp_path / "home"
    restricted = make_kb(tmp_path, name="secret-kb", sensitivity="restricted")
    public = make_kb(tmp_path, name="public-kb")
    registry.add_plugin(
        home, Plugin(name="secret-kb", repo="r", path=str(restricted), sensitivity="restricted")
    )
    registry.add_plugin(
        home, Plugin(name="public-kb", repo="r", path=str(public), sensitivity="public")
    )
    path = write_proposals(tmp_path, [fact_entry(target="public-kb")])
    code, out, _ = run(
        capsys, "--home", str(home), "distill", "ingest", path,
        "--source-plugin", "secret-kb",
    )
    assert code == 0
    assert "boundary-warnings 1" in out
    cand = staging.load_candidates(home)[0]
    assert "public-kb" in cand.boundary_warning
    assert "restricted" in cand.boundary_warning


def test_no_warning_without_source_plugin_or_for_unassigned(tmp_path, capsys):
    home = tmp_path / "home"
    public = make_kb(tmp_path, name="public-kb")
    registry.add_plugin(
        home, Plugin(name="public-kb", repo="r", path=str(public), sensitivity="public")
    )
    path = write_proposals(
        tmp_path, [fact_entry(target="public-kb"), fact_entry(target=None, topic="misc-topic")]
    )
    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert "boundary-warnings 0" in out
    for cand in staging.load_candidates(home):
        assert cand.boundary_warning == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_distill_ingest_annotations.py -v`
Expected: FAIL — `similar_to == ""` where a value is expected; `--source-plugin` unrecognized; summary lacks `boundary-warnings`.

- [ ] **Step 3: Modify `_distill_ingest` in `core/mneme_core/cli.py`**

Add to the `ingest` subparser: `p_ing.add_argument("--source-plugin", default="")`.

Inside `_distill_ingest`, before the proposal loop:

```python
    from . import routing

    scope_by_name = {s.name: s for s in routing.scopes(home)}
    source_scope = scope_by_name.get(args.source_plugin)
    index_conn = None
    db_file = paths.db_path(home)
    if db_file.exists():
        try:
            from mneme_index import db as index_db

            index_conn = index_db.open_db_readonly(db_file)
        except MnemeError:
            index_conn = None
    boundary_count = 0
```

In the loop, after the `status` decision and before constructing the Candidate:

```python
        similar_to = ""
        if index_conn is not None:
            try:
                from mneme_index import search as index_search

                probe = p.description if p.type == "skill" else p.text
                hits = index_search.search(index_conn, probe, k=1)
                if hits:
                    similar_to = hits[0]["id"]
            except MnemeError:
                similar_to = ""
        warning = ""
        target_scope = scope_by_name.get(p.target)
        if source_scope is not None and target_scope is not None:
            warning = routing.boundary_warning(source_scope.sensitivity, target_scope)
            if warning:
                boundary_count += 1
```

Thread the values into the Candidate (`similar_to=similar_to, boundary_warning=warning`). After the loop, close `index_conn` if open, and extend the summary line with `f"  boundary-warnings {boundary_count}"`.

Update Task 9's summary-format tests? No — they assert substrings (`"staged 2" in out`), which the extended line still satisfies. Do not modify them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_distill_ingest_annotations.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_distill_ingest_annotations.py
git commit -m "feat: ingest annotations — similarity and sensitivity boundaries"
```

---

### Task 11: Scaffold polish — carried Plan 03 minors

**Files:**
- Modify: `core/mneme_core/templates.py`, `core/mneme_core/scaffold.py`
- Create: `tests/core/test_scaffold_polish.py`

**Interfaces:**
- Consumes: the post-audit Plan 03 state — `templates.render_json` exists and `scaffold.create()` renders the two JSON manifests through it with an in-memory `json.loads` invariant; READ both files first and integrate.
- Produces: (a) `PLUGIN_JSON` gains `"author": { "name": "$owner" }`; (b) `MARKETPLACE_JSON` gains a top-level `"description": "$description"`; (c) `scaffold.create()` writes an empty `facts/.gitkeep` so the facts directory survives the initial git commit.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_scaffold_polish.py`:

```python
import json
import subprocess

from mneme_core import scaffold, templates


SUBS = dict(
    name="acme-knowledge",
    description="Institutional knowledge for the Acme widget platform",
    owner="acme-maintainers",
    sensitivity="internal",
    mode="pr",
)


def test_plugin_json_has_author():
    data = json.loads(templates.render_json(templates.PLUGIN_JSON, **SUBS))
    assert data["author"]["name"] == "acme-maintainers"


def test_marketplace_json_has_description():
    data = json.loads(templates.render_json(templates.MARKETPLACE_JSON, **SUBS))
    assert data["description"] == SUBS["description"]


def test_facts_dir_tracked_in_initial_commit(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "tracked-knowledge", owner="demo")
    tracked = subprocess.run(
        ["git", "-C", str(target), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "facts/.gitkeep" in tracked
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_scaffold_polish.py -v`
Expected: FAIL — `KeyError: 'author'`, `KeyError: 'description'`, and `facts/.gitkeep` absent from `git ls-files`.

- [ ] **Step 3: Implement**

In `core/mneme_core/templates.py`, update the two constants (keep them `render_json`-compatible — no other structural change):

- `PLUGIN_JSON`: add `"author": { "name": "$owner" },` after the `"version"` line.
- `MARKETPLACE_JSON`: add `"description": "$description",` after the `"name"` line.

In `core/mneme_core/scaffold.py`, in `create()` where `facts/` is created, replace the bare `mkdir` with:

```python
    facts_dir = target / "facts"
    facts_dir.mkdir(exist_ok=True)
    (facts_dir / ".gitkeep").write_text("", encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_scaffold_polish.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS (existing template tests assert specific keys and are unaffected by additions; if any asserts an exact whole-document match, update it minimally and record the deviation).

```bash
git add core/mneme_core/templates.py core/mneme_core/scaffold.py tests/core/test_scaffold_polish.py
git commit -m "fix: scaffold polish — manifest metadata and tracked facts dir"
```

---

## Verification (end of plan)

1. `python3 -m pytest -v` — entire suite green (Plan 03's count + this plan's new tests).
2. End-to-end distillation flow without any LLM (structured fixtures stand in for the distiller agent):
   ```bash
   export MNEME_HOME=$(mktemp -d)
   bin/mneme init
   bin/mneme new e2e-knowledge --owner demo
   bin/mneme context                          # brief + "- e2e-knowledge [internal/pr]: ..."
   bin/mneme flag "solved the widget deploy race after two dead ends"
   bin/mneme distill prepare --transcript /tmp/fake.jsonl | python3 -c "import json,sys; b=json.load(sys.stdin); print(b['flag_count']); assert 'proposals' in b['prompt']"
   cat > /tmp/props.json <<'EOF'
   {"proposals": [
     {"type": "skill", "edit": "new", "target": "e2e-knowledge",
      "name": "deploy-widget", "description": "Use when deploying the widget service",
      "procedure": "1. preflight\n2. cutover", "failure_pattern": "restart loop hits LB cache",
      "confidence": 0.9, "rationale": "verified this session"},
     {"type": "fact", "edit": "new", "target": "e2e-knowledge", "topic": "staging-env",
      "category": "gotcha", "text": "the staging key is AKIAIOSFODNN7EXAMPLE",
      "tags": ["staging"], "confidence": 0.4, "rationale": "seen once"}
   ]}
   EOF
   bin/mneme distill ingest /tmp/props.json --source e2e@demo --clear-flags
   # -> staged 1  quarantined 1  ...  boundary-warnings 0
   bin/mneme stage list --all                 # one staged skill, one quarantined fact
   ```
3. `git log --oneline` shows one commit per task (11 new commits).

## Out of scope for Plan 04 (later plans)

- The distiller agent definition + hook wiring (Stop/PreCompact → `prepare | claude -p | ingest`) — Plan 06 (Claude Code adapter), which also owns transcript-reading behavior.
- Harvest (`/mneme:share` review flow, branch/commit/PR plumbing, applying `update` deltas to knowledge repos) — Plan 05.
- Correction-loop candidate typing beyond the `knowledge-issue` flag kind — arrives with harvest, which is where corrections become PRs.
- Staleness sweep (`/mneme:verify`) — Plan 05/06 territory per the spec's maintain phase.
