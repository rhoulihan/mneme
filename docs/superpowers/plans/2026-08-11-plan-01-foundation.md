# Mneme Plan 01 — Foundation (core CLI + local state) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the harness-neutral core of mneme — local state layout, registry, flag capture, unit formats, staging, secret scanning, and schema lint — behind a deterministic `bin/mneme` CLI, fully tested.

**Architecture:** A stdlib-only Python package `mneme_core` under `core/`, exposed through a zero-install launcher `bin/mneme` (sys.path insertion — the plugin runs from its checkout, never pip-installed). Every module takes an explicit `home: Path` so tests use tmp dirs. The LLM layers (distiller, harvest, routing prompts) come in later plans; this plan is the deterministic substrate they call.

**Tech Stack:** Python ≥3.10, stdlib only at runtime (`json`, `re`, `hashlib`, `dataclasses`, `pathlib`, `datetime`, `math`, `argparse`). Dev-only: `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-11-mneme-design.md` (§4 architecture, §5 formats, §7.1 capture, §7.2 gate primitives). Plan 02+ will cover `mneme-index`, scaffold factory, distiller, harvest, and the Claude Code adapter.

## Global Constraints

- Python ≥3.10; no runtime dependencies outside the standard library. `pytest` is the only dev dependency.
- License: Apache-2.0 (LICENSE at repo root; no per-file headers).
- All names user-visible in formats are kebab-case (`^[a-z0-9]+(-[a-z0-9]+)*$`).
- Timestamps are ISO-8601 UTC, seconds precision (`datetime.now(timezone.utc).isoformat(timespec="seconds")`).
- All file I/O is UTF-8, explicit `encoding="utf-8"`.
- CLI exit codes: `0` success · `1` error (MnemeError or usage) · `2` findings (scan blockers / lint errors).
- Core modules never touch the network.
- Fact categories: `decision | constraint | gotcha | runbook-note | reference`. Candidate types: `skill | fact`. Edit kinds: `new | update`. Registry modes: `pr | commit`. Sensitivities: `public | internal | restricted`. Flag kinds: `golden-path | knowledge-issue`.
- **Strict TDD, no exceptions:** write the failing test, run it and watch it fail for the expected reason, write the minimal implementation, watch it pass, run the whole suite, then commit. Never write implementation before its test exists and has failed.
- Run tests with `python3 -m pytest` from the repo root.
- Secret-scan test fixtures must use documented example credentials (e.g., AWS's `AKIAIOSFODNN7EXAMPLE`), never plausible live-looking values.

## File Structure

```
mneme/
├── LICENSE                     # Apache-2.0 (Task 1)
├── pyproject.toml              # metadata + pytest config (Task 1)
├── conftest.py                 # sys.path → core/ (Task 1)
├── .gitignore                  # + Python artifacts (Task 1)
├── bin/
│   └── mneme                   # zero-install launcher (Task 1, wired Task 10)
├── core/
│   └── mneme_core/
│       ├── __init__.py         # __version__ (Task 1)
│       ├── errors.py           # MnemeError (Task 1)
│       ├── paths.py            # state layout (Task 2)
│       ├── units.py            # frontmatter, ids, fact bullets (Tasks 3–4)
│       ├── registry.py         # registry.json CRUD (Task 5)
│       ├── flags.py            # session flag capture (Task 6)
│       ├── scan.py             # secret/PII scan (Task 7)
│       ├── staging.py          # candidates + declined ledger (Task 8)
│       ├── lint.py             # skill/fact schema lint (Task 9)
│       └── cli.py              # argparse dispatch (Task 10)
└── tests/
    └── core/
        ├── test_smoke.py       # Task 1
        ├── test_paths.py       # Task 2
        ├── test_units_frontmatter.py  # Task 3
        ├── test_units_ids.py   # Task 4
        ├── test_registry.py    # Task 5
        ├── test_flags.py       # Task 6
        ├── test_scan.py        # Task 7
        ├── test_staging.py     # Task 8
        ├── test_lint.py        # Task 9
        └── test_cli.py         # Task 10
```

---

### Task 1: Project scaffolding

**Files:**
- Create: `LICENSE`, `pyproject.toml`, `conftest.py`, `core/mneme_core/__init__.py`, `core/mneme_core/errors.py`, `bin/mneme`, `tests/core/test_smoke.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: package `mneme_core` importable in tests via `conftest.py`; `mneme_core.__version__: str`; `mneme_core.errors.MnemeError(Exception)`; executable `bin/mneme` (fully wired in Task 10 — until then it exits 1 with "cli not implemented").

- [ ] **Step 1: Write the failing smoke test**

Create `tests/core/test_smoke.py`:

```python
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_package_imports_and_has_version():
    import mneme_core

    assert isinstance(mneme_core.__version__, str)
    assert mneme_core.__version__.count(".") == 2


def test_mneme_error_is_exception():
    from mneme_core.errors import MnemeError

    assert issubclass(MnemeError, Exception)


def test_launcher_is_executable_python():
    launcher = REPO_ROOT / "bin" / "mneme"
    assert launcher.exists()
    result = subprocess.run(
        [sys.executable, str(launcher)], capture_output=True, text=True
    )
    # Until Task 10 wires the CLI, the launcher must fail gracefully, not traceback.
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core'` (conftest missing) or import errors.

- [ ] **Step 3: Create the scaffolding**

Create `conftest.py` at repo root:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))
```

Create `pyproject.toml`:

```toml
[project]
name = "mneme"
version = "0.1.0"
description = "Knowledge-mining engine for AI coding agents"
requires-python = ">=3.10"
license = { text = "Apache-2.0" }

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["core"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

Create `core/mneme_core/__init__.py`:

```python
"""mneme core — deterministic substrate for the mneme knowledge-mining engine."""

__version__ = "0.1.0"
```

Create `core/mneme_core/errors.py`:

```python
class MnemeError(Exception):
    """Base error for mneme operations; message text is user-facing."""
```

Create `bin/mneme`:

```python
#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

try:
    from mneme_core.cli import main
except ImportError:
    print("mneme: cli not implemented", file=sys.stderr)
    sys.exit(1)

sys.exit(main())
```

Make it executable: `chmod +x bin/mneme`

Fetch the license: `curl -fsSL https://www.apache.org/licenses/LICENSE-2.0.txt -o LICENSE`
Verify: `head -2 LICENSE` shows "Apache License" / "Version 2.0, January 2004". (If offline, stop and report — do not hand-write license text.)

Append to `.gitignore`:

```
# Python
__pycache__/
*.pyc
.pytest_cache/
build/
dist/
*.egg-info/
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_smoke.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: project scaffolding — package layout, launcher, license"
```

---

### Task 2: State layout (`paths.py`)

**Files:**
- Create: `core/mneme_core/paths.py`, `tests/core/test_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces (all take `home: Path`, return `Path`): `mneme_home() -> Path` (reads `$MNEME_HOME`, else `~/.mneme`), `staging_dir`, `quarantine_dir`, `repos_dir`, `logs_dir`, `registry_path`, `declined_path`, `flags_path`, `db_path`, `ensure_layout(home) -> Path` (mkdir -p all dirs, returns home).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_paths.py`:

```python
from pathlib import Path

from mneme_core import paths


def test_mneme_home_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MNEME_HOME", str(tmp_path / "custom"))
    assert paths.mneme_home() == tmp_path / "custom"


def test_mneme_home_default(monkeypatch):
    monkeypatch.delenv("MNEME_HOME", raising=False)
    assert paths.mneme_home() == Path.home() / ".mneme"


def test_layout_paths_derive_from_home(tmp_path):
    assert paths.staging_dir(tmp_path) == tmp_path / "staging"
    assert paths.quarantine_dir(tmp_path) == tmp_path / "staging" / "quarantine"
    assert paths.repos_dir(tmp_path) == tmp_path / "repos"
    assert paths.logs_dir(tmp_path) == tmp_path / "logs"
    assert paths.registry_path(tmp_path) == tmp_path / "registry.json"
    assert paths.declined_path(tmp_path) == tmp_path / "declined.jsonl"
    assert paths.flags_path(tmp_path) == tmp_path / "staging" / "flags.jsonl"
    assert paths.db_path(tmp_path) == tmp_path / "mneme.db"


def test_ensure_layout_creates_dirs_and_is_idempotent(tmp_path):
    home = tmp_path / "m"
    returned = paths.ensure_layout(home)
    assert returned == home
    for d in (
        home,
        paths.staging_dir(home),
        paths.quarantine_dir(home),
        paths.repos_dir(home),
        paths.logs_dir(home),
    ):
        assert d.is_dir()
    paths.ensure_layout(home)  # second call must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_paths.py -v`
Expected: FAIL — `ImportError: cannot import name 'paths'` / `ModuleNotFoundError`.

- [ ] **Step 3: Implement `core/mneme_core/paths.py`**

```python
"""Filesystem layout for mneme local state (spec §4.1)."""
from __future__ import annotations

import os
from pathlib import Path


def mneme_home() -> Path:
    env = os.environ.get("MNEME_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".mneme"


def staging_dir(home: Path) -> Path:
    return home / "staging"


def quarantine_dir(home: Path) -> Path:
    return staging_dir(home) / "quarantine"


def repos_dir(home: Path) -> Path:
    return home / "repos"


def logs_dir(home: Path) -> Path:
    return home / "logs"


def registry_path(home: Path) -> Path:
    return home / "registry.json"


def declined_path(home: Path) -> Path:
    return home / "declined.jsonl"


def flags_path(home: Path) -> Path:
    return staging_dir(home) / "flags.jsonl"


def db_path(home: Path) -> Path:
    return home / "mneme.db"


def ensure_layout(home: Path) -> Path:
    for d in (home, staging_dir(home), quarantine_dir(home), repos_dir(home), logs_dir(home)):
        d.mkdir(parents=True, exist_ok=True)
    return home
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_paths.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/paths.py tests/core/test_paths.py
git commit -m "feat: local state layout (paths module)"
```

---

### Task 3: Frontmatter parse/serialize (`units.py`, part 1)

**Files:**
- Create: `core/mneme_core/units.py`, `tests/core/test_units_frontmatter.py`

**Interfaces:**
- Consumes: `mneme_core.errors.MnemeError`.
- Produces: `parse_frontmatter(text: str) -> tuple[dict, str]` and `serialize_frontmatter(meta: dict, body: str) -> str`. Supported YAML subset: flat `key: value` (plain/quoted), one level of nested maps, lists of scalars (`- item`), block scalars (`>` folds with spaces, `|` keeps newlines). Values parse as `str`, `dict[str, str]`, or `list[str]`. `({}, text)` when no frontmatter. `MnemeError` on unterminated block or unparseable line.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_units_frontmatter.py`:

```python
import pytest

from mneme_core.errors import MnemeError
from mneme_core.units import parse_frontmatter, serialize_frontmatter


def test_no_frontmatter_returns_empty_meta_and_full_text():
    meta, body = parse_frontmatter("just a body\n")
    assert meta == {}
    assert body == "just a body\n"


def test_flat_and_quoted_values():
    text = '---\nname: deploy-widget\ntitle: "a: colon value"\n---\nbody here'
    meta, body = parse_frontmatter(text)
    assert meta == {"name": "deploy-widget", "title": "a: colon value"}
    assert body == "body here"


def test_nested_map_one_level():
    text = "---\nmetadata:\n  mneme-type: skill\n  mneme-captured: 2026-08-11\n---\n"
    meta, _ = parse_frontmatter(text)
    assert meta["metadata"] == {"mneme-type": "skill", "mneme-captured": "2026-08-11"}


def test_list_of_scalars():
    text = "---\ntags:\n  - alpha\n  - beta\n---\n"
    meta, _ = parse_frontmatter(text)
    assert meta["tags"] == ["alpha", "beta"]


def test_folded_and_literal_block_scalars():
    folded = "---\ndescription: >\n  line one\n  line two\n---\n"
    meta, _ = parse_frontmatter(folded)
    assert meta["description"] == "line one line two"

    literal = "---\nnotes: |\n  line one\n  line two\n---\n"
    meta, _ = parse_frontmatter(literal)
    assert meta["notes"] == "line one\nline two"


def test_unterminated_frontmatter_raises():
    with pytest.raises(MnemeError):
        parse_frontmatter("---\nname: x\nno closing delim")


def test_unparseable_line_raises():
    with pytest.raises(MnemeError):
        parse_frontmatter("---\n???\n---\n")


def test_round_trip():
    meta = {
        "name": "deploy-widget",
        "description": "Use when deploying widget-service",
        "metadata": {"mneme-type": "skill"},
        "tags": ["alpha", "beta"],
        "notes": "line one\nline two",
    }
    body = "# Procedure\n\nSteps here.\n"
    text = serialize_frontmatter(meta, body)
    meta2, body2 = parse_frontmatter(text)
    assert meta2 == meta
    assert body2 == body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_units_frontmatter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.units'`.

- [ ] **Step 3: Implement `core/mneme_core/units.py`**

```python
"""Knowledge unit formats: frontmatter, unit ids, fact bullets (spec §5)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .errors import MnemeError

_FM_DELIM = "---"
_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
_NESTED_RE = re.compile(r"^\s+([A-Za-z0-9_-]+):\s*(.*)$")

KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FM_DELIM:
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FM_DELIM:
            end = i
            break
    if end is None:
        raise MnemeError("unterminated frontmatter block")
    meta = _parse_block(lines[1:end])
    body_lines = lines[end + 1 :]
    body = "\n".join(body_lines)
    if text.endswith("\n") and body:
        body += "\n"
    return meta, body


def _strip_quotes(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1].replace('\\"', '"')
    return v


def _collect_indented(lines: list[str], start: int) -> tuple[list[str], int]:
    block: list[str] = []
    i = start
    while i < len(lines) and (lines[i].startswith(" ") or not lines[i].strip()):
        if lines[i].strip():
            block.append(lines[i])
        i += 1
    return block, i


def _parse_block(lines: list[str]) -> dict:
    meta: dict = {}
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw.startswith(" "):
            raise MnemeError(f"unexpected indentation in frontmatter: {raw!r}")
        m = _KEY_RE.match(raw)
        if not m:
            raise MnemeError(f"cannot parse frontmatter line: {raw!r}")
        key, val = m.group(1), m.group(2).strip()
        if val in (">", "|"):
            block, i = _collect_indented(lines, i + 1)
            joiner = " " if val == ">" else "\n"
            meta[key] = joiner.join(s.strip() for s in block).strip()
        elif val:
            meta[key] = _strip_quotes(val)
            i += 1
        else:
            block, i = _collect_indented(lines, i + 1)
            if block and block[0].lstrip().startswith("- "):
                meta[key] = [_strip_quotes(s.lstrip()[2:]) for s in block]
            elif block:
                sub: dict = {}
                for s in block:
                    sm = _NESTED_RE.match(s)
                    if not sm:
                        raise MnemeError(f"cannot parse nested frontmatter line: {s!r}")
                    sub[sm.group(1)] = _strip_quotes(sm.group(2))
                meta[key] = sub
            else:
                meta[key] = ""
    return meta


def _quote_if_needed(v: str) -> str:
    if v == "" or v != v.strip() or ":" in v or v[:1] in ("'", '"', ">", "|", "-", "#"):
        return '"' + v.replace('"', '\\"') + '"'
    return v


def serialize_frontmatter(meta: dict, body: str) -> str:
    out = [_FM_DELIM]
    for key, val in meta.items():
        if isinstance(val, dict):
            out.append(f"{key}:")
            for k, v in val.items():
                out.append(f"  {k}: {_quote_if_needed(str(v))}")
        elif isinstance(val, list):
            out.append(f"{key}:")
            for v in val:
                out.append(f"  - {_quote_if_needed(str(v))}")
        elif isinstance(val, str) and "\n" in val:
            out.append(f"{key}: |")
            for line in val.splitlines():
                out.append(f"  {line}")
        else:
            out.append(f"{key}: {_quote_if_needed(str(val))}")
    out.append(_FM_DELIM)
    return "\n".join(out) + "\n" + body
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_units_frontmatter.py -v` → all PASS. (Round-trip test exercises the parse/serialize contract; if it fails, fix the implementation, not the test.)

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/units.py tests/core/test_units_frontmatter.py
git commit -m "feat: frontmatter parser/serializer (YAML subset, stdlib-only)"
```

---

### Task 4: Unit ids, hashes, fact bullets (`units.py`, part 2)

**Files:**
- Modify: `core/mneme_core/units.py` (append)
- Create: `tests/core/test_units_ids.py`

**Interfaces:**
- Consumes: Task 3's module.
- Produces: `FACT_CATEGORIES: frozenset[str]`; `@dataclass FactBullet(category: str, text: str, tags: list[str], verified: str | None, line_no: int)` with property `topic_key: str`; `parse_bullet_line(line: str, line_no: int) -> FactBullet` (raises `MnemeError` on malformed); `parse_fact_bullets(body: str) -> list[FactBullet]` (parses every line starting `- [`, raises on first malformed); `normalize_topic_key(text: str) -> str` (lowercase, alnum words, first 6, kebab-joined); `content_hash(text: str) -> str` (sha256 of whitespace-normalized text, first 12 hex chars); `skill_unit_id(skill_name: str) -> str` (`skills/<name>`); `fact_unit_id(topic_file_stem: str, bullet_text: str) -> str` (`facts/<stem>#<topic-key>`).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_units_ids.py`:

```python
import pytest

from mneme_core.errors import MnemeError
from mneme_core.units import (
    FACT_CATEGORIES,
    content_hash,
    fact_unit_id,
    normalize_topic_key,
    parse_bullet_line,
    parse_fact_bullets,
    skill_unit_id,
)


def test_fact_categories():
    assert FACT_CATEGORIES == {"decision", "constraint", "gotcha", "runbook-note", "reference"}


def test_parse_full_bullet():
    line = "- [constraint] Staging DB resets nightly at 04:00 UTC #staging #db (verified: 2026-08-11)"
    b = parse_bullet_line(line, 7)
    assert b.category == "constraint"
    assert b.text == "Staging DB resets nightly at 04:00 UTC"
    assert b.tags == ["staging", "db"]
    assert b.verified == "2026-08-11"
    assert b.line_no == 7


def test_parse_minimal_bullet():
    b = parse_bullet_line("- [gotcha] v2 API truncates batch writes", 1)
    assert b.category == "gotcha"
    assert b.tags == []
    assert b.verified is None


def test_malformed_bullet_raises():
    with pytest.raises(MnemeError):
        parse_bullet_line("- [gotcha no closing bracket", 3)


def test_parse_fact_bullets_skips_non_bullet_lines():
    body = "## Topic\n\n- [decision] Use Oracle 26ai #db\nprose line\n- [gotcha] Thing #x\n"
    bullets = parse_fact_bullets(body)
    assert [b.category for b in bullets] == ["decision", "gotcha"]
    assert bullets[0].line_no == 3
    assert bullets[1].line_no == 5


def test_normalize_topic_key_first_six_words():
    key = normalize_topic_key("Staging DB resets nightly at 04:00 UTC every day")
    assert key == "staging-db-resets-nightly-at-04"


def test_topic_key_property_matches_function():
    b = parse_bullet_line("- [constraint] Staging DB resets nightly #staging", 1)
    assert b.topic_key == normalize_topic_key("Staging DB resets nightly")


def test_content_hash_normalizes_whitespace():
    assert content_hash("a  b\nc") == content_hash("a b c")
    assert len(content_hash("x")) == 12
    assert content_hash("x") != content_hash("y")


def test_unit_ids():
    assert skill_unit_id("deploy-widget") == "skills/deploy-widget"
    assert fact_unit_id("staging-env", "Staging DB resets nightly") == (
        "facts/staging-env#staging-db-resets-nightly"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_units_ids.py -v`
Expected: FAIL — `ImportError: cannot import name 'FACT_CATEGORIES'`.

- [ ] **Step 3: Append to `core/mneme_core/units.py`**

```python
FACT_CATEGORIES = frozenset({"decision", "constraint", "gotcha", "runbook-note", "reference"})

_BULLET_RE = re.compile(
    r"^- \[(?P<category>[a-z-]+)\]\s+(?P<text>.+?)"
    r"(?P<tags>(?:\s+#[\w-]+)*)"
    r"(?:\s+\(verified:\s*(?P<verified>\d{4}-\d{2}-\d{2})\))?\s*$"
)


def normalize_topic_key(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words[:6])


@dataclass
class FactBullet:
    category: str
    text: str
    tags: list[str]
    verified: str | None
    line_no: int

    @property
    def topic_key(self) -> str:
        return normalize_topic_key(self.text)


def parse_bullet_line(line: str, line_no: int) -> FactBullet:
    m = _BULLET_RE.match(line)
    if not m:
        raise MnemeError(f"malformed fact bullet at line {line_no}: {line!r}")
    tags = re.findall(r"#([\w-]+)", m.group("tags") or "")
    return FactBullet(
        category=m.group("category"),
        text=m.group("text").strip(),
        tags=tags,
        verified=m.group("verified"),
        line_no=line_no,
    )


def parse_fact_bullets(body: str) -> list[FactBullet]:
    bullets: list[FactBullet] = []
    for n, line in enumerate(body.splitlines(), start=1):
        if line.startswith("- ["):
            bullets.append(parse_bullet_line(line, n))
    return bullets


def content_hash(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def skill_unit_id(skill_name: str) -> str:
    return f"skills/{skill_name}"


def fact_unit_id(topic_file_stem: str, bullet_text: str) -> str:
    return f"facts/{topic_file_stem}#{normalize_topic_key(bullet_text)}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_units_ids.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/units.py tests/core/test_units_ids.py
git commit -m "feat: unit ids, content hashes, fact bullet parsing"
```

---

### Task 5: Registry (`registry.py`)

**Files:**
- Create: `core/mneme_core/registry.py`, `tests/core/test_registry.py`

**Interfaces:**
- Consumes: `paths.registry_path`, `paths.ensure_layout`, `units.KEBAB_RE`, `MnemeError`.
- Produces: `MODES = frozenset({"pr", "commit"})`; `SENSITIVITIES = frozenset({"public", "internal", "restricted"})`; `@dataclass Plugin(name: str, repo: str, path: str, mode: str = "pr", sensitivity: str = "internal", exclusions: list[str] = [])` with `validate() -> None`; `load_registry(home: Path) -> list[Plugin]`; `save_registry(home: Path, plugins: list[Plugin]) -> None` (validates all, writes `{"version": 1, "plugins": [...]}` JSON, indent 2, trailing newline); `add_plugin(home, plugin) -> None` (MnemeError on duplicate name); `remove_plugin(home, name) -> None` (MnemeError if absent); `get_plugin(home, name) -> Plugin | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_registry.py`:

```python
import json

import pytest

from mneme_core import paths, registry
from mneme_core.errors import MnemeError
from mneme_core.registry import Plugin


def make(name="acme-knowledge", **kw):
    defaults = dict(repo="git@github.com:acme/acme-knowledge.git", path="/tmp/x")
    defaults.update(kw)
    return Plugin(name=name, **defaults)


def test_load_empty_registry(tmp_path):
    assert registry.load_registry(tmp_path) == []


def test_add_and_get_round_trip(tmp_path):
    registry.add_plugin(tmp_path, make())
    loaded = registry.get_plugin(tmp_path, "acme-knowledge")
    assert loaded is not None
    assert loaded.repo == "git@github.com:acme/acme-knowledge.git"
    assert loaded.mode == "pr"
    assert loaded.sensitivity == "internal"
    assert loaded.exclusions == []


def test_registry_file_shape(tmp_path):
    registry.add_plugin(tmp_path, make())
    data = json.loads(paths.registry_path(tmp_path).read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["plugins"][0]["name"] == "acme-knowledge"


def test_duplicate_name_rejected(tmp_path):
    registry.add_plugin(tmp_path, make())
    with pytest.raises(MnemeError):
        registry.add_plugin(tmp_path, make())


def test_remove_plugin(tmp_path):
    registry.add_plugin(tmp_path, make())
    registry.remove_plugin(tmp_path, "acme-knowledge")
    assert registry.get_plugin(tmp_path, "acme-knowledge") is None
    with pytest.raises(MnemeError):
        registry.remove_plugin(tmp_path, "acme-knowledge")


@pytest.mark.parametrize(
    "kw",
    [
        {"name": "Bad_Name"},
        {"mode": "push"},
        {"sensitivity": "secret"},
        {"repo": ""},
    ],
)
def test_validation_rejects_bad_fields(tmp_path, kw):
    with pytest.raises(MnemeError):
        registry.add_plugin(tmp_path, make(**{"name": "ok-name", **kw}))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.registry'`.

- [ ] **Step 3: Implement `core/mneme_core/registry.py`**

```python
"""Registered knowledge plugins — flat-file source of truth (spec §4.2)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths
from .errors import MnemeError
from .units import KEBAB_RE

MODES = frozenset({"pr", "commit"})
SENSITIVITIES = frozenset({"public", "internal", "restricted"})


@dataclass
class Plugin:
    name: str
    repo: str
    path: str
    mode: str = "pr"
    sensitivity: str = "internal"
    exclusions: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not KEBAB_RE.match(self.name):
            raise MnemeError(f"plugin name must be kebab-case: {self.name!r}")
        if not self.repo:
            raise MnemeError("plugin repo must not be empty")
        if self.mode not in MODES:
            raise MnemeError(f"mode must be one of {sorted(MODES)}: {self.mode!r}")
        if self.sensitivity not in SENSITIVITIES:
            raise MnemeError(
                f"sensitivity must be one of {sorted(SENSITIVITIES)}: {self.sensitivity!r}"
            )


def load_registry(home: Path) -> list[Plugin]:
    p = paths.registry_path(home)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return [Plugin(**entry) for entry in data.get("plugins", [])]


def save_registry(home: Path, plugins: list[Plugin]) -> None:
    for pl in plugins:
        pl.validate()
    paths.ensure_layout(home)
    payload = {"version": 1, "plugins": [asdict(pl) for pl in plugins]}
    paths.registry_path(home).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def add_plugin(home: Path, plugin: Plugin) -> None:
    plugins = load_registry(home)
    if any(p.name == plugin.name for p in plugins):
        raise MnemeError(f"plugin already registered: {plugin.name}")
    plugins.append(plugin)
    save_registry(home, plugins)


def remove_plugin(home: Path, name: str) -> None:
    plugins = load_registry(home)
    kept = [p for p in plugins if p.name != name]
    if len(kept) == len(plugins):
        raise MnemeError(f"plugin not registered: {name}")
    save_registry(home, kept)


def get_plugin(home: Path, name: str) -> Plugin | None:
    for p in load_registry(home):
        if p.name == name:
            return p
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_registry.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/registry.py tests/core/test_registry.py
git commit -m "feat: knowledge-plugin registry (flat JSON source of truth)"
```

---

### Task 6: Flag capture (`flags.py`)

**Files:**
- Create: `core/mneme_core/flags.py`, `tests/core/test_flags.py`

**Interfaces:**
- Consumes: `paths.flags_path`, `paths.ensure_layout`, `MnemeError`.
- Produces: `KINDS = frozenset({"golden-path", "knowledge-issue"})`; `add_flag(home: Path, text: str, kind: str = "golden-path", session: str | None = None) -> dict` (appends JSONL record `{"ts", "session", "kind", "text"}`; session falls back to `$CLAUDE_SESSION_ID` then `"unknown"`; MnemeError on empty text or unknown kind); `read_flags(home: Path) -> list[dict]`; `clear_flags(home: Path) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_flags.py`:

```python
import pytest

from mneme_core import flags
from mneme_core.errors import MnemeError


def test_add_and_read_flags(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    rec = flags.add_flag(tmp_path, "solved the widget deploy race", session="s-1")
    assert rec["kind"] == "golden-path"
    assert rec["session"] == "s-1"
    assert rec["ts"].endswith("+00:00")

    flags.add_flag(tmp_path, "docs said X but reality is Y", kind="knowledge-issue")
    all_flags = flags.read_flags(tmp_path)
    assert len(all_flags) == 2
    assert all_flags[1]["kind"] == "knowledge-issue"
    assert all_flags[1]["session"] == "unknown"


def test_session_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "env-session")
    rec = flags.add_flag(tmp_path, "note")
    assert rec["session"] == "env-session"


def test_empty_text_rejected(tmp_path):
    with pytest.raises(MnemeError):
        flags.add_flag(tmp_path, "   ")


def test_unknown_kind_rejected(tmp_path):
    with pytest.raises(MnemeError):
        flags.add_flag(tmp_path, "x", kind="misc")


def test_read_missing_and_clear(tmp_path):
    assert flags.read_flags(tmp_path) == []
    flags.add_flag(tmp_path, "x")
    flags.clear_flags(tmp_path)
    assert flags.read_flags(tmp_path) == []
    flags.clear_flags(tmp_path)  # idempotent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_flags.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.flags'`.

- [ ] **Step 3: Implement `core/mneme_core/flags.py`**

```python
"""In-session flag capture — the near-zero-overhead noticing primitive (spec §7.1)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .errors import MnemeError

KINDS = frozenset({"golden-path", "knowledge-issue"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_flag(
    home: Path, text: str, kind: str = "golden-path", session: str | None = None
) -> dict:
    if kind not in KINDS:
        raise MnemeError(f"flag kind must be one of {sorted(KINDS)}: {kind!r}")
    if not text.strip():
        raise MnemeError("flag text must not be empty")
    record = {
        "ts": _now(),
        "session": session or os.environ.get("CLAUDE_SESSION_ID", "unknown"),
        "kind": kind,
        "text": text.strip(),
    }
    paths.ensure_layout(home)
    with paths.flags_path(home).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def read_flags(home: Path) -> list[dict]:
    p = paths.flags_path(home)
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def clear_flags(home: Path) -> None:
    p = paths.flags_path(home)
    if p.exists():
        p.unlink()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_flags.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/flags.py tests/core/test_flags.py
git commit -m "feat: session flag capture (golden-path / knowledge-issue)"
```

---

### Task 7: Secret/PII scan (`scan.py`)

**Files:**
- Create: `core/mneme_core/scan.py`, `tests/core/test_scan.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `BLOCK = "block"`, `WARN = "warn"`; `@dataclass Finding(rule: str, severity: str, line_no: int, excerpt: str)` — excerpt is redacted (first 4 chars + `…`); `scan_text(text: str) -> list[Finding]`; `shannon_entropy(s: str) -> float`; `has_blockers(findings: list[Finding]) -> bool`. Rules (severity): `aws-access-key` (block), `github-token` (block), `slack-token` (block), `private-key` (block), `jwt` (block), `assigned-secret` (block), `high-entropy` (block, entropy ≥ 4.0 on assigned values ≥ 20 chars), `email` (warn).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_scan.py` (fixtures use documented example credentials only):

```python
from mneme_core import scan


def rules(findings):
    return {f.rule for f in findings}


def test_aws_example_key_blocks():
    findings = scan.scan_text("key = AKIAIOSFODNN7EXAMPLE")
    assert "aws-access-key" in rules(findings)
    assert scan.has_blockers(findings)


def test_github_token_blocks():
    fake = "ghp_" + "a" * 36
    findings = scan.scan_text(f"token: {fake}")
    assert "github-token" in rules(findings)


def test_private_key_header_blocks():
    findings = scan.scan_text("-----BEGIN RSA PRIVATE KEY-----")
    assert "private-key" in rules(findings)


def test_assigned_secret_blocks():
    findings = scan.scan_text('api_key = "abcd1234efgh5678"')
    assert "assigned-secret" in rules(findings)


def test_high_entropy_assignment_blocks():
    findings = scan.scan_text("secret_blob = kJ8vQ2xN9pL4mR7tW3yZ6bC1dF5gH0aS")
    assert "high-entropy" in rules(findings)


def test_email_warns_but_does_not_block():
    findings = scan.scan_text("contact rick.houlihan@gmail.com for access")
    assert rules(findings) == {"email"}
    assert not scan.has_blockers(findings)


def test_clean_text_no_findings():
    text = "- [gotcha] v2 API truncates batch writes over 500 items #api\n"
    assert scan.scan_text(text) == []


def test_excerpt_is_redacted_and_line_numbered():
    findings = scan.scan_text("line one\nkey = AKIAIOSFODNN7EXAMPLE\n")
    f = next(x for x in findings if x.rule == "aws-access-key")
    assert f.line_no == 2
    assert f.excerpt.startswith("AKIA")
    assert f.excerpt.endswith("…")
    assert "EXAMPLE" not in f.excerpt


def test_shannon_entropy_bounds():
    assert scan.shannon_entropy("") == 0.0
    assert scan.shannon_entropy("aaaa") == 0.0
    assert scan.shannon_entropy("abcdefgh") == 3.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_scan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.scan'`.

- [ ] **Step 3: Implement `core/mneme_core/scan.py`**

```python
"""Deterministic secret/PII scanning for the machine gate (spec §7.2, §8)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

BLOCK = "block"
WARN = "warn"

_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("aws-access-key", BLOCK, re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "github-token",
        BLOCK,
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    ),
    ("slack-token", BLOCK, re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private-key", BLOCK, re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "jwt",
        BLOCK,
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    (
        "assigned-secret",
        BLOCK,
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['\"]?"
            r"(?P<value>[A-Za-z0-9+/_=-]{12,})"
        ),
    ),
    ("email", WARN, re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
]

_ASSIGN_RE = re.compile(r"[:=]\s*['\"]?(?P<value>[A-Za-z0-9+/_=-]{20,})['\"]?")


@dataclass
class Finding:
    rule: str
    severity: str
    line_no: int
    excerpt: str


def _redact(match_text: str) -> str:
    return (match_text[:4] + "…") if len(match_text) > 4 else "…"


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


def scan_text(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for n, line in enumerate(text.splitlines(), start=1):
        for rule, severity, pattern in _RULES:
            for m in pattern.finditer(line):
                findings.append(Finding(rule, severity, n, _redact(m.group(0))))
        for m in _ASSIGN_RE.finditer(line):
            value = m.group("value")
            if shannon_entropy(value) >= 4.0:
                findings.append(Finding("high-entropy", BLOCK, n, _redact(value)))
    return findings


def has_blockers(findings: list[Finding]) -> bool:
    return any(f.severity == BLOCK for f in findings)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_scan.py -v` → all PASS. (If `test_high_entropy_assignment_blocks` fails, check the fixture value's entropy is ≥ 4.0 by computing `shannon_entropy` in a REPL — adjust the implementation threshold logic only if it miscomputes entropy; never weaken the fixture.)

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/scan.py tests/core/test_scan.py
git commit -m "feat: deterministic secret/PII scan with severity tiers"
```

---

### Task 8: Staging + declined ledger (`staging.py`)

**Files:**
- Create: `core/mneme_core/staging.py`, `tests/core/test_staging.py`

**Interfaces:**
- Consumes: `paths` (staging_dir, quarantine_dir, declined_path, ensure_layout), `units.parse_frontmatter`, `units.serialize_frontmatter`, `units.content_hash`, `MnemeError`.
- Produces: `TYPES = frozenset({"skill", "fact"})`; `EDITS = frozenset({"new", "update"})`; `UNASSIGNED = "unassigned"`; `@dataclass Candidate(id: str, type: str, edit: str, target: str, body: str, confidence: float = 0.5, rationale: str = "", target_unit: str = "", provenance: dict = {}, status: str = "staged")` with `validate()`; `candidate_id(type_: str, target: str, body: str) -> str` (`<type>-<content_hash(target + "\n" + body)>`); `write_candidate(home, cand) -> Path` (staged → `staging/<id>.md`, quarantined → `staging/quarantine/<id>.md`); `load_candidates(home, include_quarantined: bool = False) -> list[Candidate]` (sorted by id); `remove_candidate(home, cand_id) -> None` (checks both dirs; MnemeError if absent); `quarantine(home, cand_id) -> Path` (moves staged file, rewrites status); `decline(home, cand: Candidate, reason: str) -> None` (appends `{"id", "hash", "reason", "ts"}` to declined.jsonl, removes the staged file if present); `is_declined(home, body: str) -> bool` (matches by `content_hash(body)`).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_staging.py`:

```python
import pytest

from mneme_core import paths, staging
from mneme_core.errors import MnemeError
from mneme_core.staging import Candidate, candidate_id


def make(target="acme-knowledge", body="# Skill body\n", **kw):
    cid = candidate_id("skill", target, body)
    defaults = dict(
        id=cid,
        type="skill",
        edit="new",
        target=target,
        body=body,
        confidence=0.8,
        rationale="hard-won deploy fix",
        provenance={"source": "repo@session-1", "captured": "2026-08-11"},
    )
    defaults.update(kw)
    return Candidate(**defaults)


def test_candidate_id_is_stable_and_type_prefixed():
    a = candidate_id("skill", "t", "body")
    assert a == candidate_id("skill", "t", "body")
    assert a.startswith("skill-")
    assert a != candidate_id("fact", "t", "body")


def test_write_and_load_round_trip(tmp_path):
    cand = make()
    path = staging.write_candidate(tmp_path, cand)
    assert path.parent == paths.staging_dir(tmp_path)
    loaded = staging.load_candidates(tmp_path)
    assert len(loaded) == 1
    got = loaded[0]
    assert got == cand


def test_update_requires_target_unit(tmp_path):
    cand = make(edit="update")
    with pytest.raises(MnemeError):
        staging.write_candidate(tmp_path, cand)
    ok = make(edit="update", target_unit="skills/deploy-widget")
    staging.write_candidate(tmp_path, ok)


def test_validation_rejects_bad_enum_values(tmp_path):
    with pytest.raises(MnemeError):
        staging.write_candidate(tmp_path, make(type="note"))
    with pytest.raises(MnemeError):
        staging.write_candidate(tmp_path, make(edit="patch"))
    with pytest.raises(MnemeError):
        staging.write_candidate(tmp_path, make(body="  "))


def test_quarantine_moves_and_marks(tmp_path):
    cand = make()
    staging.write_candidate(tmp_path, cand)
    qpath = staging.quarantine(tmp_path, cand.id)
    assert qpath.parent == paths.quarantine_dir(tmp_path)
    assert staging.load_candidates(tmp_path) == []
    quarantined = staging.load_candidates(tmp_path, include_quarantined=True)
    assert len(quarantined) == 1
    assert quarantined[0].status == "quarantined"


def test_remove_candidate(tmp_path):
    cand = make()
    staging.write_candidate(tmp_path, cand)
    staging.remove_candidate(tmp_path, cand.id)
    assert staging.load_candidates(tmp_path) == []
    with pytest.raises(MnemeError):
        staging.remove_candidate(tmp_path, cand.id)


def test_decline_records_and_removes(tmp_path):
    cand = make()
    staging.write_candidate(tmp_path, cand)
    assert not staging.is_declined(tmp_path, cand.body)
    staging.decline(tmp_path, cand, "not institutional knowledge")
    assert staging.is_declined(tmp_path, cand.body)
    assert staging.load_candidates(tmp_path) == []
    # same body re-proposed under a different target still matches the ledger
    assert staging.is_declined(tmp_path, "# Skill body\n")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_staging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.staging'`.

- [ ] **Step 3: Implement `core/mneme_core/staging.py`**

```python
"""Candidate staging area and declined ledger (spec §7.2–7.3)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import paths, units
from .errors import MnemeError

TYPES = frozenset({"skill", "fact"})
EDITS = frozenset({"new", "update"})
UNASSIGNED = "unassigned"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Candidate:
    id: str
    type: str
    edit: str
    target: str
    body: str
    confidence: float = 0.5
    rationale: str = ""
    target_unit: str = ""
    provenance: dict = field(default_factory=dict)
    status: str = "staged"

    def validate(self) -> None:
        if self.type not in TYPES:
            raise MnemeError(f"candidate type must be one of {sorted(TYPES)}: {self.type!r}")
        if self.edit not in EDITS:
            raise MnemeError(f"candidate edit must be one of {sorted(EDITS)}: {self.edit!r}")
        if self.edit == "update" and not self.target_unit:
            raise MnemeError("update candidates must set target_unit")
        if not self.body.strip():
            raise MnemeError("candidate body must not be empty")
        if self.status not in ("staged", "quarantined"):
            raise MnemeError(f"unknown candidate status: {self.status!r}")


def candidate_id(type_: str, target: str, body: str) -> str:
    digest = units.content_hash(target + "\n" + body)
    return f"{type_}-{digest}"


def _to_text(cand: Candidate) -> str:
    meta = {
        "id": cand.id,
        "type": cand.type,
        "edit": cand.edit,
        "target": cand.target,
        "confidence": str(cand.confidence),
        "rationale": cand.rationale,
        "target-unit": cand.target_unit,
        "status": cand.status,
        "provenance": {k: str(v) for k, v in cand.provenance.items()},
    }
    return units.serialize_frontmatter(meta, cand.body)


def _from_text(text: str) -> Candidate:
    meta, body = units.parse_frontmatter(text)
    return Candidate(
        id=str(meta.get("id", "")),
        type=str(meta.get("type", "")),
        edit=str(meta.get("edit", "")),
        target=str(meta.get("target", UNASSIGNED)),
        body=body,
        confidence=float(meta.get("confidence", "0.5")),
        rationale=str(meta.get("rationale", "")),
        target_unit=str(meta.get("target-unit", "")),
        provenance=dict(meta.get("provenance", {})),
        status=str(meta.get("status", "staged")),
    )


def _find(home: Path, cand_id: str) -> Path | None:
    for d in (paths.staging_dir(home), paths.quarantine_dir(home)):
        p = d / f"{cand_id}.md"
        if p.exists():
            return p
    return None


def write_candidate(home: Path, cand: Candidate) -> Path:
    cand.validate()
    paths.ensure_layout(home)
    directory = (
        paths.quarantine_dir(home) if cand.status == "quarantined" else paths.staging_dir(home)
    )
    path = directory / f"{cand.id}.md"
    path.write_text(_to_text(cand), encoding="utf-8")
    return path


def load_candidates(home: Path, include_quarantined: bool = False) -> list[Candidate]:
    dirs = [paths.staging_dir(home)]
    if include_quarantined:
        dirs.append(paths.quarantine_dir(home))
    out: list[Candidate] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            out.append(_from_text(p.read_text(encoding="utf-8")))
    return sorted(out, key=lambda c: c.id)


def remove_candidate(home: Path, cand_id: str) -> None:
    p = _find(home, cand_id)
    if p is None:
        raise MnemeError(f"no staged candidate with id: {cand_id}")
    p.unlink()


def quarantine(home: Path, cand_id: str) -> Path:
    p = _find(home, cand_id)
    if p is None:
        raise MnemeError(f"no staged candidate with id: {cand_id}")
    cand = _from_text(p.read_text(encoding="utf-8"))
    cand.status = "quarantined"
    p.unlink()
    return write_candidate(home, cand)


def _read_declined(home: Path) -> list[dict]:
    p = paths.declined_path(home)
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def decline(home: Path, cand: Candidate, reason: str) -> None:
    paths.ensure_layout(home)
    record = {
        "id": cand.id,
        "hash": units.content_hash(cand.body),
        "reason": reason,
        "ts": _now(),
    }
    with paths.declined_path(home).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    existing = _find(home, cand.id)
    if existing is not None:
        existing.unlink()


def is_declined(home: Path, body: str) -> bool:
    h = units.content_hash(body)
    return any(rec.get("hash") == h for rec in _read_declined(home))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_staging.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/staging.py tests/core/test_staging.py
git commit -m "feat: candidate staging, quarantine, and declined ledger"
```

---

### Task 9: Schema lint (`lint.py`)

**Files:**
- Create: `core/mneme_core/lint.py`, `tests/core/test_lint.py`

**Interfaces:**
- Consumes: `units.parse_frontmatter`, `units.parse_bullet_line`, `units.FACT_CATEGORIES`, `units.KEBAB_RE`, `MnemeError`.
- Produces: `MAX_DESCRIPTION = 1024`; `@dataclass LintIssue(path: str, line: int, code: str, severity: str, message: str)`; `lint_skill(skill_dir: Path) -> list[LintIssue]`; `lint_fact_file(path: Path) -> list[LintIssue]`; `lint_repo(root: Path) -> list[LintIssue]` (walks `skills/*/` and `facts/*.md`); `has_errors(issues: list[LintIssue]) -> bool`. Codes: MN001 SKILL.md missing/no frontmatter (error), MN002 name missing or not kebab-case (error), MN003 name ≠ directory (error), MN004 description missing (error), MN005 description > 1024 chars (error), MN006 malformed fact bullet (error), MN007 unknown fact category (error), MN008 bullet missing verified date (warn), MN009 fact file missing `topic` frontmatter (error), MN010 unparseable frontmatter (error).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_lint.py`:

```python
from mneme_core import lint


def write_skill(root, dirname, frontmatter):
    d = root / "skills" / dirname
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(frontmatter, encoding="utf-8")
    return d


def codes(issues):
    return [i.code for i in issues]


def test_valid_skill_passes(tmp_path):
    d = write_skill(
        tmp_path,
        "deploy-widget",
        "---\nname: deploy-widget\ndescription: Use when deploying widgets\n---\nBody\n",
    )
    assert lint.lint_skill(d) == []


def test_missing_skill_md(tmp_path):
    d = tmp_path / "skills" / "empty-skill"
    d.mkdir(parents=True)
    issues = lint.lint_skill(d)
    assert codes(issues) == ["MN001"]


def test_name_mismatch_and_bad_name(tmp_path):
    d = write_skill(
        tmp_path, "deploy-widget", "---\nname: other-name\ndescription: d\n---\n"
    )
    assert "MN003" in codes(lint.lint_skill(d))
    d2 = write_skill(tmp_path, "bad", "---\nname: Bad_Name\ndescription: d\n---\n")
    assert "MN002" in codes(lint.lint_skill(d2))


def test_description_missing_and_too_long(tmp_path):
    d = write_skill(tmp_path, "no-desc", "---\nname: no-desc\n---\n")
    assert "MN004" in codes(lint.lint_skill(d))
    long_desc = "x" * 1025
    d2 = write_skill(
        tmp_path, "long-desc", f"---\nname: long-desc\ndescription: {long_desc}\n---\n"
    )
    assert "MN005" in codes(lint.lint_skill(d2))


def test_fact_file_lint(tmp_path):
    facts = tmp_path / "facts"
    facts.mkdir()
    f = facts / "staging-env.md"
    f.write_text(
        "---\ntopic: staging-env\n---\n"
        "- [constraint] DB resets nightly #db (verified: 2026-08-11)\n"
        "- [bogus] unknown category (verified: 2026-08-11)\n"
        "- [gotcha] no verified date\n"
        "- [broken no close\n",
        encoding="utf-8",
    )
    issues = lint.lint_fact_file(f)
    assert codes(issues) == ["MN007", "MN008", "MN006"]
    severities = {i.code: i.severity for i in issues}
    assert severities["MN008"] == "warn"
    assert severities["MN006"] == "error"
    # line numbers are absolute within the file (frontmatter offset applied)
    mn007 = next(i for i in issues if i.code == "MN007")
    assert mn007.line == 5


def test_fact_file_missing_topic(tmp_path):
    f = tmp_path / "notopic.md"
    f.write_text("- [gotcha] thing (verified: 2026-08-11)\n", encoding="utf-8")
    assert "MN009" in codes(lint.lint_fact_file(f))


def test_lint_repo_walks_both_tiers(tmp_path):
    write_skill(
        tmp_path,
        "good-skill",
        "---\nname: good-skill\ndescription: fine\n---\n",
    )
    facts = tmp_path / "facts"
    facts.mkdir()
    (facts / "t.md").write_text(
        "---\ntopic: t\n---\n- [bogus] x (verified: 2026-08-11)\n", encoding="utf-8"
    )
    issues = lint.lint_repo(tmp_path)
    assert codes(issues) == ["MN007"]
    assert lint.has_errors(issues)


def test_has_errors_ignores_warnings(tmp_path):
    facts = tmp_path / "facts"
    facts.mkdir()
    (facts / "t.md").write_text(
        "---\ntopic: t\n---\n- [gotcha] no date\n", encoding="utf-8"
    )
    issues = lint.lint_repo(tmp_path)
    assert codes(issues) == ["MN008"]
    assert not lint.has_errors(issues)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_lint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.lint'`.

- [ ] **Step 3: Implement `core/mneme_core/lint.py`**

```python
"""Schema lint for knowledge units — machines settle format (spec §5, §7.4)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import units
from .errors import MnemeError

MAX_DESCRIPTION = 1024


@dataclass
class LintIssue:
    path: str
    line: int
    code: str
    severity: str
    message: str


def lint_skill(skill_dir: Path) -> list[LintIssue]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return [LintIssue(str(skill_md), 0, "MN001", "error", "SKILL.md not found")]
    text = skill_md.read_text(encoding="utf-8")
    try:
        meta, _body = units.parse_frontmatter(text)
    except MnemeError as e:
        return [LintIssue(str(skill_md), 0, "MN010", "error", str(e))]
    if not meta:
        return [LintIssue(str(skill_md), 0, "MN001", "error", "missing frontmatter")]
    issues: list[LintIssue] = []
    name = str(meta.get("name", ""))
    if not name or not units.KEBAB_RE.match(name):
        issues.append(
            LintIssue(str(skill_md), 0, "MN002", "error", f"name missing or not kebab-case: {name!r}")
        )
    elif name != skill_dir.name:
        issues.append(
            LintIssue(
                str(skill_md), 0, "MN003", "error",
                f"name {name!r} does not match directory {skill_dir.name!r}",
            )
        )
    description = str(meta.get("description", ""))
    if not description:
        issues.append(LintIssue(str(skill_md), 0, "MN004", "error", "description missing"))
    elif len(description) > MAX_DESCRIPTION:
        issues.append(
            LintIssue(
                str(skill_md), 0, "MN005", "error",
                f"description exceeds {MAX_DESCRIPTION} chars ({len(description)})",
            )
        )
    return issues


def lint_fact_file(path: Path) -> list[LintIssue]:
    text = path.read_text(encoding="utf-8")
    try:
        meta, body = units.parse_frontmatter(text)
    except MnemeError as e:
        return [LintIssue(str(path), 0, "MN010", "error", str(e))]
    issues: list[LintIssue] = []
    if "topic" not in meta:
        issues.append(LintIssue(str(path), 0, "MN009", "error", "missing 'topic' frontmatter"))
    offset = len(text.splitlines()) - len(body.splitlines())
    for n, line in enumerate(body.splitlines(), start=1):
        if not line.startswith("- ["):
            continue
        abs_line = offset + n
        try:
            bullet = units.parse_bullet_line(line, n)
        except MnemeError:
            issues.append(
                LintIssue(str(path), abs_line, "MN006", "error", f"malformed fact bullet: {line!r}")
            )
            continue
        if bullet.category not in units.FACT_CATEGORIES:
            issues.append(
                LintIssue(
                    str(path), abs_line, "MN007", "error",
                    f"unknown fact category: {bullet.category!r}",
                )
            )
        if bullet.verified is None:
            issues.append(
                LintIssue(str(path), abs_line, "MN008", "warn", "bullet missing verified date")
            )
    return issues


def lint_repo(root: Path) -> list[LintIssue]:
    issues: list[LintIssue] = []
    skills_dir = root / "skills"
    if skills_dir.is_dir():
        for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            issues.extend(lint_skill(d))
    facts_dir = root / "facts"
    if facts_dir.is_dir():
        for f in sorted(facts_dir.glob("*.md")):
            issues.extend(lint_fact_file(f))
    return issues


def has_errors(issues: list[LintIssue]) -> bool:
    return any(i.severity == "error" for i in issues)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_lint.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/lint.py tests/core/test_lint.py
git commit -m "feat: skill and fact schema lint with error/warn tiers"
```

---

### Task 10: CLI (`cli.py`) + launcher integration

**Files:**
- Create: `core/mneme_core/cli.py`, `tests/core/test_cli.py`

**Interfaces:**
- Consumes: everything above — `paths`, `registry` (Plugin, add_plugin, load_registry, remove_plugin), `flags.add_flag`, `scan.scan_text`, `scan.has_blockers`, `lint.lint_repo`, `lint.lint_skill`, `lint.lint_fact_file`, `lint.has_errors`, `staging.load_candidates`, `mneme_core.__version__`, `MnemeError`.
- Produces: `main(argv: list[str] | None = None) -> int`. Subcommands (all honor global `--home PATH` before the subcommand; default `paths.mneme_home()`):
  - `mneme --version` → prints version, exit 0.
  - `mneme init` → `ensure_layout`, save empty registry if absent, print home path.
  - `mneme home` → print resolved home path.
  - `mneme flag TEXT [--kind golden-path|knowledge-issue] [--session ID]` → add flag, print `flagged`.
  - `mneme registry add NAME --repo URL [--path P] [--mode pr|commit] [--sensitivity S] [--exclude GLOB]...` → default path `repos/<name>` under home.
  - `mneme registry list` → one line per plugin: `name  mode  sensitivity  repo`.
  - `mneme registry remove NAME`.
  - `mneme stage list [--all]` → one line per candidate: `id  type/edit  target  status`.
  - `mneme scan PATH` (or `-` for stdin) → print `rule severity line excerpt` per finding; exit 2 when `has_blockers`, else 0.
  - `mneme lint PATH` → repo root, skill dir (contains SKILL.md), or fact `.md` file; print `path:line code severity message`; exit 2 when `has_errors`, else 0.
  - Any `MnemeError` → message on stderr prefixed `mneme: `, exit 1.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_cli.py`:

```python
import subprocess
import sys
from pathlib import Path

import pytest

from mneme_core import staging
from mneme_core.cli import main
from mneme_core.staging import Candidate, candidate_id

REPO_ROOT = Path(__file__).resolve().parents[2]


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_version(capsys):
    code, out, _ = run(capsys, "--version")
    assert code == 0
    assert out.strip().count(".") == 2


def test_init_and_home(tmp_path, capsys):
    code, out, _ = run(capsys, "--home", str(tmp_path), "init")
    assert code == 0
    assert str(tmp_path) in out
    assert (tmp_path / "registry.json").exists()
    code, out, _ = run(capsys, "--home", str(tmp_path), "home")
    assert code == 0
    assert out.strip() == str(tmp_path)


def test_flag_roundtrip(tmp_path, capsys):
    code, out, _ = run(
        capsys, "--home", str(tmp_path), "flag", "learned a thing", "--session", "s1"
    )
    assert code == 0
    assert "flagged" in out
    flags_file = tmp_path / "staging" / "flags.jsonl"
    assert "learned a thing" in flags_file.read_text(encoding="utf-8")


def test_registry_add_list_remove(tmp_path, capsys):
    code, _, _ = run(
        capsys, "--home", str(tmp_path), "registry", "add", "acme-knowledge",
        "--repo", "git@github.com:acme/k.git", "--mode", "commit",
    )
    assert code == 0
    code, out, _ = run(capsys, "--home", str(tmp_path), "registry", "list")
    assert code == 0
    assert "acme-knowledge" in out and "commit" in out
    code, _, _ = run(capsys, "--home", str(tmp_path), "registry", "remove", "acme-knowledge")
    assert code == 0
    code, out, _ = run(capsys, "--home", str(tmp_path), "registry", "list")
    assert "acme-knowledge" not in out


def test_registry_duplicate_is_error(tmp_path, capsys):
    run(capsys, "--home", str(tmp_path), "registry", "add", "a-b", "--repo", "r")
    code, _, err = run(capsys, "--home", str(tmp_path), "registry", "add", "a-b", "--repo", "r")
    assert code == 1
    assert "mneme:" in err


def test_stage_list(tmp_path, capsys):
    body = "# B\n"
    cand = Candidate(
        id=candidate_id("skill", "acme-knowledge", body),
        type="skill", edit="new", target="acme-knowledge", body=body,
    )
    staging.write_candidate(tmp_path, cand)
    code, out, _ = run(capsys, "--home", str(tmp_path), "stage", "list")
    assert code == 0
    assert cand.id in out


def test_scan_exit_codes(tmp_path, capsys):
    dirty = tmp_path / "dirty.txt"
    dirty.write_text("key = AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")
    code, out, _ = run(capsys, "scan", str(dirty))
    assert code == 2
    assert "aws-access-key" in out
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing secret here\n", encoding="utf-8")
    code, _, _ = run(capsys, "scan", str(clean))
    assert code == 0


def test_lint_exit_codes(tmp_path, capsys):
    skill = tmp_path / "skills" / "bad-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: wrong-name\ndescription: d\n---\n", encoding="utf-8")
    code, out, _ = run(capsys, "lint", str(tmp_path))
    assert code == 2
    assert "MN003" in out


def test_launcher_end_to_end(tmp_path):
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "mneme"), "--home", str(tmp_path), "init"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert (tmp_path / "registry.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.cli'`.

- [ ] **Step 3: Implement `core/mneme_core/cli.py`**

```python
"""mneme CLI — deterministic operations behind bin/mneme (spec §4.1)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, flags, lint, paths, registry, scan, staging
from .errors import MnemeError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mneme")
    # store_true, not action="version": the latter raises SystemExit, which would
    # escape main() and break in-process testing of the exit-code contract.
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--home", type=Path, default=None, help="override MNEME_HOME")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init")
    sub.add_parser("home")

    p_flag = sub.add_parser("flag")
    p_flag.add_argument("text")
    p_flag.add_argument("--kind", default="golden-path", choices=sorted(flags.KINDS))
    p_flag.add_argument("--session", default=None)

    p_reg = sub.add_parser("registry")
    reg_sub = p_reg.add_subparsers(dest="registry_command", required=True)
    p_add = reg_sub.add_parser("add")
    p_add.add_argument("name")
    p_add.add_argument("--repo", required=True)
    p_add.add_argument("--path", default=None)
    p_add.add_argument("--mode", default="pr", choices=sorted(registry.MODES))
    p_add.add_argument(
        "--sensitivity", default="internal", choices=sorted(registry.SENSITIVITIES)
    )
    p_add.add_argument("--exclude", action="append", default=[])
    reg_sub.add_parser("list")
    p_rm = reg_sub.add_parser("remove")
    p_rm.add_argument("name")

    p_stage = sub.add_parser("stage")
    stage_sub = p_stage.add_subparsers(dest="stage_command", required=True)
    p_slist = stage_sub.add_parser("list")
    p_slist.add_argument("--all", action="store_true")

    p_scan = sub.add_parser("scan")
    p_scan.add_argument("path")

    p_lint = sub.add_parser("lint")
    p_lint.add_argument("path", type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    home = args.home if args.home is not None else paths.mneme_home()

    try:
        if args.command == "init":
            paths.ensure_layout(home)
            if not paths.registry_path(home).exists():
                registry.save_registry(home, [])
            print(str(home))
            return 0
        if args.command == "home":
            print(str(home))
            return 0
        if args.command == "flag":
            flags.add_flag(home, args.text, kind=args.kind, session=args.session)
            print("flagged")
            return 0
        if args.command == "registry":
            return _registry_cmd(home, args)
        if args.command == "stage":
            for cand in staging.load_candidates(home, include_quarantined=args.all):
                print(f"{cand.id}  {cand.type}/{cand.edit}  {cand.target}  {cand.status}")
            return 0
        if args.command == "scan":
            return _scan_cmd(args.path)
        if args.command == "lint":
            return _lint_cmd(args.path)
        parser.print_help()
        return 1
    except MnemeError as e:
        print(f"mneme: {e}", file=sys.stderr)
        return 1


def _registry_cmd(home: Path, args: argparse.Namespace) -> int:
    if args.registry_command == "add":
        plugin_path = args.path or str(paths.repos_dir(home) / args.name)
        registry.add_plugin(
            home,
            registry.Plugin(
                name=args.name,
                repo=args.repo,
                path=plugin_path,
                mode=args.mode,
                sensitivity=args.sensitivity,
                exclusions=args.exclude,
            ),
        )
        print(f"registered {args.name}")
        return 0
    if args.registry_command == "list":
        for p in registry.load_registry(home):
            print(f"{p.name}  {p.mode}  {p.sensitivity}  {p.repo}")
        return 0
    if args.registry_command == "remove":
        registry.remove_plugin(home, args.name)
        print(f"removed {args.name}")
        return 0
    return 1


def _scan_cmd(path_arg: str) -> int:
    if path_arg == "-":
        text = sys.stdin.read()
    else:
        text = Path(path_arg).read_text(encoding="utf-8")
    findings = scan.scan_text(text)
    for f in findings:
        print(f"{f.rule} {f.severity} {f.line_no} {f.excerpt}")
    return 2 if scan.has_blockers(findings) else 0


def _lint_cmd(target: Path) -> int:
    if target.is_dir() and (target / "SKILL.md").exists():
        issues = lint.lint_skill(target)
    elif target.is_dir():
        issues = lint.lint_repo(target)
    elif target.suffix == ".md":
        issues = lint.lint_fact_file(target)
    else:
        raise MnemeError(f"cannot lint: {target}")
    for i in issues:
        print(f"{i.path}:{i.line} {i.code} {i.severity} {i.message}")
    return 2 if lint.has_errors(issues) else 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_cli.py -v` → all PASS (including the launcher end-to-end test — `bin/mneme` now finds `mneme_core.cli`).

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_cli.py
git commit -m "feat: mneme CLI — init, flag, registry, stage, scan, lint"
```

---

## Verification (end of plan)

1. `python3 -m pytest -v` — entire suite green.
2. `bin/mneme --version` prints `0.1.0`.
3. Real-flow smoke (use a scratch home):
   ```bash
   export MNEME_HOME=$(mktemp -d)
   bin/mneme init
   bin/mneme registry add demo-knowledge --repo git@github.com:example/demo-knowledge.git
   bin/mneme registry list
   bin/mneme flag "learned: the v2 API truncates batches over 500"
   printf 'key = AKIAIOSFODNN7EXAMPLE\n' | bin/mneme scan -   # exits 2
   bin/mneme stage list                                        # empty, exits 0
   ```
4. `git log --oneline` shows one commit per task, all tests committed alongside their implementation.

## Out of scope for Plan 01 (later plans)

- Plan 02: `mneme-index` (SQLite FTS5 build/search over installed knowledge, registry mirror, `mneme search`).
- Plan 03: scaffold factory (`mneme new` — knowledge-repo generation: manifests, MNEME.md, CI, CODEOWNERS).
- Plan 04: routing + distiller (agent definition, machine-gate orchestration using Plan 01 primitives).
- Plan 05: harvest (`/mneme:share` — review flow, git branch/commit/PR plumbing).
- Plan 06: Claude Code adapter (plugin manifest, hooks, commands, behavioral skills).
- Plan 07: end-to-end harness + dogfood knowledge repo.
