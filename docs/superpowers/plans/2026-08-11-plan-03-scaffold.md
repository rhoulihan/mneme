# Mneme Plan 03 — Hardening + Scaffold Factory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the eight carried findings from Plan 02's audit (Tasks 1–7), then build the scaffold factory (`mneme new`) that generates governed knowledge-plugin repos — templates, git init, registration, and the mechanical `knowledge-index` router-skill regenerator (Tasks 8–11).

**Architecture:** Hardening lands where the defects live (`mneme_core/cli.py`, `mneme_index/{db,build,search,cli}.py`, `mneme_core/units.py`). The factory is two new `mneme_core` modules: `templates.py` (pure string constants + a `string.Template` renderer — no logic) and `scaffold.py` (writes the tree, verifies it lints clean, `git init` + first commit, registers the plugin). Index-skill regeneration is deterministic code, never an LLM (spec §5.3).

**Tech Stack:** Python ≥3.10 stdlib (`string.Template`, `subprocess` for git, `sqlite3`, `argparse`, `json`). Dev-only: `pytest`. Requires `git` ≥2.28 on PATH (for `init -b main`).

**Spec:** `docs/superpowers/specs/2026-08-11-mneme-design.md` §5.1 (scaffold), §5.3 (facts consumption / mechanical regeneration), §6.1–6.2 (index fields, db enable, query surface), §8 (governance). Plans 01–02 delivered everything this consumes.

## Global Constraints

- Plan 01 + Plan 02 Global Constraints all still apply (stdlib-only runtime, UTF-8, exit codes 0/1/2, strict TDD, kebab-case, `mneme_index` import boundary, parameterized SQL, WSL `git update-index --chmod=+x` for new executables).
- The full existing suite (149 tests) must stay green after every task. Modifying an existing test file is allowed ONLY where a task explicitly lists it under **Files: Modify** (a deliberate contract change) — never to weaken an assertion.
- Scaffolded repos must pass `bin/mneme lint <repo>` with zero error-severity issues, by construction, enforced inside `scaffold.create()` itself.
- Templates are rendered with `string.Template` (`$name` placeholders). Rendered JSON must `json.loads` cleanly.
- All git side effects in scaffold code run via `subprocess.run([...], check=True, capture_output=True)` with explicit `-c user.name=mneme -c user.email=mneme@localhost` identity fallbacks so tests never depend on machine git config.
- Run tests with `python3 -m pytest` from the repo root.

## File Structure

```
core/mneme_core/
├── cli.py         # Task 1 (db query hardening), Task 7 (db enable/disable), Task 11 (new)
├── units.py       # Task 4 (topic-key fallback)
├── templates.py   # Task 8 (new)
└── scaffold.py    # Tasks 9–10 (new)
core/mneme_index/
├── db.py          # Task 6 (schema v2: summary column)
├── build.py       # Task 3 (BOM, resolved roots), Task 6 (summary extraction)
├── search.py      # Task 2 (LIKE escaping), Task 6 (summary in hits)
└── cli.py         # Task 5 (usage exit codes)
tests/core/
├── test_db_query_hardening.py   # Task 1
├── test_units_topic_fallback.py # Task 4
├── test_db_enable.py            # Task 7
├── test_templates.py            # Task 8
├── test_scaffold.py             # Task 9
├── test_regenerate.py           # Task 10
└── test_cli_new.py              # Task 11
tests/index/
├── test_tag_escaping.py         # Task 2
├── test_build_hardening.py      # Task 3
├── test_index_cli_errors.py     # Task 5
├── test_summary_field.py        # Task 6
└── test_search.py               # Task 6 (Modify: hit-key contract adds 'summary')
```

**Canonical `units` row after Task 6** (order is load-bearing; Tasks 1–5 still use the Plan 02 11-tuple until Task 6 lands):
`(plugin, id, kind, name, description, summary, category, tags, path, line, verified, hash)` — indices 0–11. FTS mirror row: `(plugin, id, name, description, summary, tags)` = indices `0, 1, 3, 4, 5, 7`.

---

### Task 1: Restrict `mneme db query` to SELECT with an authorizer guard

**Files:**
- Modify: `core/mneme_core/cli.py` (`_db_cmd` and a new module-level helper)
- Create: `tests/core/test_db_query_hardening.py`

**Interfaces:**
- Consumes: existing `_db_cmd`, `_require_index_db`, `MnemeError`.
- Produces: `_db_cmd` rejects any statement that does not start with `SELECT` (case-insensitive, leading whitespace ignored) with `MnemeError("only SELECT queries are allowed")`; additionally installs `_readonly_authorizer` on the connection (denies `SQLITE_ATTACH`, `SQLITE_DETACH`, `SQLITE_PRAGMA`; allows everything else — defense in depth on top of the ro connection). Multi-statement input surfaces as `MnemeError` (exit 1) via the existing `sqlite3.Error` wrap.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_db_query_hardening.py`:

```python
from mneme_core import registry
from mneme_core.cli import main
from mneme_core.registry import Plugin


def make_tree(root):
    d = root / "skills" / "deploy-widget"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: deploy-widget\ndescription: Use when deploying the widget service\n---\nBody\n",
        encoding="utf-8",
    )
    return root


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def setup_indexed_home(tmp_path, capsys):
    home = tmp_path / "home"
    tree = make_tree(tmp_path / "clone")
    registry.add_plugin(home, Plugin(name="acme-knowledge", repo="r", path=str(tree)))
    run(capsys, "--home", str(home), "index", "rebuild")
    return home


def test_select_still_works(tmp_path, capsys):
    home = setup_indexed_home(tmp_path, capsys)
    code, out, _ = run(capsys, "--home", str(home), "db", "query", "SELECT COUNT(*) FROM units")
    assert code == 0
    assert out.strip() == "1"


def test_attach_rejected_and_no_file_created(tmp_path, capsys):
    home = setup_indexed_home(tmp_path, capsys)
    evil = tmp_path / "evil.db"
    code, _, err = run(
        capsys, "--home", str(home), "db", "query", f"ATTACH DATABASE '{evil}' AS evil"
    )
    assert code == 1
    assert "only SELECT queries are allowed" in err
    assert not evil.exists()


def test_leading_whitespace_and_case_allowed(tmp_path, capsys):
    home = setup_indexed_home(tmp_path, capsys)
    code, out, _ = run(capsys, "--home", str(home), "db", "query", "  select 1")
    assert code == 0
    assert out.strip() == "1"


def test_multi_statement_rejected(tmp_path, capsys):
    home = setup_indexed_home(tmp_path, capsys)
    code, _, err = run(
        capsys, "--home", str(home), "db", "query", "SELECT 1; DELETE FROM units"
    )
    assert code == 1
    assert "mneme:" in err


def test_non_select_rejected(tmp_path, capsys):
    home = setup_indexed_home(tmp_path, capsys)
    code, _, err = run(capsys, "--home", str(home), "db", "query", "PRAGMA user_version = 9")
    assert code == 1
    assert "only SELECT queries are allowed" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_db_query_hardening.py -v`
Expected: `test_attach_rejected_and_no_file_created` and `test_non_select_rejected` FAIL (ATTACH currently succeeds; PRAGMA currently executes).

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

Add a module-level helper (near the other `_`-helpers):

```python
def _readonly_authorizer(action, arg1, arg2, dbname, source):
    import sqlite3

    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK
```

Rewrite `_db_cmd` (keep its existing signature and the surrounding `MnemeError` contract):

```python
def _db_cmd(home: Path, args: argparse.Namespace) -> int:
    import sqlite3

    if not args.sql.lstrip().lower().startswith("select"):
        raise MnemeError("only SELECT queries are allowed")
    conn = _require_index_db(home)
    conn.set_authorizer(_readonly_authorizer)
    try:
        try:
            rows = conn.execute(args.sql).fetchall()
        except sqlite3.Error as e:
            raise MnemeError(f"query failed: {e}")
        for r in rows:
            print("\t".join(str(v) for v in tuple(r)))
    finally:
        conn.close()
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_db_query_hardening.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS (the Plan 02 read-only DELETE test now hits the SELECT-only message — if any existing test asserts the old "query failed" wording for DELETE, update that assertion to the new message and record it as the task's intended contract change).

```bash
git add core/mneme_core/cli.py tests/core/test_db_query_hardening.py
git commit -m "fix: restrict mneme db query to SELECT-only with authorizer guard"
```

---

### Task 2: Escape LIKE wildcards in the fact tag filter

**Files:**
- Modify: `core/mneme_index/search.py` (`list_facts` tag branch)
- Create: `tests/index/test_tag_escaping.py`

**Interfaces:**
- Consumes: existing `list_facts`.
- Produces: tag filtering treats `%`, `_`, and `\` in the tag literally: pattern built from an escaped tag with `ESCAPE '\'`.

- [ ] **Step 1: Write the failing tests**

Create `tests/index/test_tag_escaping.py`:

```python
import pytest

from mneme_index import build, db, search


@pytest.fixture
def conn(tmp_path):
    root = tmp_path / "tree"
    facts = root / "facts"
    facts.mkdir(parents=True)
    (facts / "t.md").write_text(
        "---\ntopic: t\n---\n"
        "- [gotcha] Underscore tagged fact #api_v2 (verified: 2026-08-11)\n"
        "- [gotcha] Plain tagged fact #apiXv2 (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    c = db.open_db(tmp_path / "i.db")
    build.index_tree(c, "p", root)
    yield c
    c.close()


def test_underscore_is_literal_not_wildcard(conn):
    rows = search.list_facts(conn, tag="api_v2")
    assert len(rows) == 1
    assert "Underscore" in rows[0]["description"]


def test_percent_matches_nothing_literal(conn):
    assert search.list_facts(conn, tag="api%") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/index/test_tag_escaping.py -v`
Expected: `test_underscore_is_literal_not_wildcard` FAILS (returns 2 rows — `_` matched `X`) and `test_percent_matches_nothing_literal` FAILS (wildcard matched).

- [ ] **Step 3: Modify the tag branch in `list_facts` (`core/mneme_index/search.py`)**

Replace:

```python
    if tag:
        sql += " AND ' ' || tags || ' ' LIKE ?"
        params.append(f"% {tag} %")
```

with:

```python
    if tag:
        escaped = tag.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql += " AND ' ' || tags || ' ' LIKE ? ESCAPE '\\'"
        params.append(f"% {escaped} %")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/index/test_tag_escaping.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_index/search.py tests/index/test_tag_escaping.py
git commit -m "fix: escape LIKE wildcards in fact tag filter"
```

---

### Task 3: BOM-tolerant reads and resolved index roots

**Files:**
- Modify: `core/mneme_index/build.py`
- Create: `tests/index/test_build_hardening.py`

**Interfaces:**
- Consumes: the post-audit `build.py` (it has a `_read_unit_text()` helper from the Plan 02 audit fix — check the file first and adapt).
- Produces: (a) unit files are read with `encoding="utf-8-sig"` so a UTF-8 BOM cannot silently hide the first bullet; (b) `index_tree` stores `str(root.resolve())` in `plugins.root` so relative CLI roots don't produce unresolvable status output.

- [ ] **Step 1: Write the failing tests**

Create `tests/index/test_build_hardening.py`:

```python
import os

from mneme_index import build, db


def test_bom_prefixed_fact_file_indexes_first_bullet(tmp_path):
    root = tmp_path / "tree"
    facts = root / "facts"
    facts.mkdir(parents=True)
    (facts / "t.md").write_bytes(
        "﻿- [gotcha] BOM fact #bom (verified: 2026-08-11)\n".encode("utf-8")
    )
    conn = db.open_db(tmp_path / "i.db")
    stats = build.index_tree(conn, "p", root)
    assert stats.facts == 1
    assert stats.skipped == []
    conn.close()


def test_root_stored_resolved(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    (root / "facts").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    conn = db.open_db(tmp_path / "i.db")
    build.index_tree(conn, "p", type(root)("tree"))
    row = conn.execute("SELECT root FROM plugins WHERE name = 'p'").fetchone()
    assert os.path.isabs(row["root"])
    assert row["root"] == str(root.resolve())
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/index/test_build_hardening.py -v`
Expected: BOM test FAILS (0 facts, nothing skipped); resolved-root test FAILS (`root` stored as `tree`).

- [ ] **Step 3: Modify `core/mneme_index/build.py`**

1. In the file-reading helper (post-audit `_read_unit_text`, or the `read_text` call sites if the helper is named differently), change `encoding="utf-8"` to `encoding="utf-8-sig"`.
2. In `index_tree`, immediately after the `root.is_dir()` guard, add `root = root.resolve()` so every downstream `relative_to(root)` and the `plugins.root` upsert use the resolved absolute path.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/index/test_build_hardening.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_index/build.py tests/index/test_build_hardening.py
git commit -m "fix: BOM-tolerant reads and resolved index roots"
```

---

### Task 4: Hash fallback for degenerate topic keys

**Files:**
- Modify: `core/mneme_core/units.py` (`normalize_topic_key`)
- Create: `tests/core/test_units_topic_fallback.py`

**Interfaces:**
- Consumes: existing `normalize_topic_key`, `content_hash`.
- Produces: when the text yields no `[a-z0-9]` words (pure CJK, emoji, symbols), `normalize_topic_key` returns `content_hash(text)[:8]` instead of `""` — so `fact_unit_id` never collapses distinct non-ASCII bullets onto the same id. ASCII behavior is unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_units_topic_fallback.py`:

```python
from mneme_core.units import content_hash, fact_unit_id, normalize_topic_key


def test_ascii_behavior_unchanged():
    assert normalize_topic_key("Staging DB resets nightly") == "staging-db-resets-nightly"


def test_cjk_text_gets_hash_fallback():
    key = normalize_topic_key("日本語のドキュメント検索")
    assert key == content_hash("日本語のドキュメント検索")[:8]
    assert key != ""


def test_distinct_cjk_bullets_get_distinct_ids():
    a = fact_unit_id("t", "日本語のドキュメント検索")
    b = fact_unit_id("t", "検索エンジンの構成")
    assert a != b
    assert not a.endswith("#")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_units_topic_fallback.py -v`
Expected: CJK tests FAIL (empty key, identical ids ending `#`).

- [ ] **Step 3: Modify `normalize_topic_key` in `core/mneme_core/units.py`**

```python
def normalize_topic_key(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    if words:
        return "-".join(words[:6])
    return content_hash(text)[:8]
```

(`content_hash` is defined later in the module; Python resolves it at call time, so ordering is fine.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_units_topic_fallback.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/units.py tests/core/test_units_topic_fallback.py
git commit -m "fix: hash fallback for degenerate topic keys"
```

---

### Task 5: `mneme-index` usage errors exit 1

**Files:**
- Modify: `core/mneme_index/cli.py`
- Create: `tests/index/test_index_cli_errors.py`

**Interfaces:**
- Consumes: existing `_build_parser`/`main`.
- Produces: usage errors (unknown subcommand, missing required argument) exit **1**, printing usage plus `mneme-index: <message>` to stderr — never argparse's default exit 2, which is reserved for findings. First check how `mneme_core/cli.py` solved the same problem after Plan 01's audit fix and mirror that mechanism; if it uses a different approach than below, mirroring it is the right call (note it in deviations). Otherwise implement:

- [ ] **Step 1: Write the failing tests**

Create `tests/index/test_index_cli_errors.py`:

```python
from mneme_index.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_unknown_subcommand_exits_1(tmp_path, capsys):
    code, _, err = run(capsys, "--db", str(tmp_path / "i.db"), "frobnicate")
    assert code == 1
    assert "mneme-index:" in err


def test_missing_required_db_exits_1(capsys):
    code, _, err = run(capsys, "status")
    assert code == 1
    assert "mneme-index:" in err


def test_missing_subcommand_exits_1(tmp_path, capsys):
    code, _, err = run(capsys, "--db", str(tmp_path / "i.db"))
    assert code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/index/test_index_cli_errors.py -v`
Expected: FAIL — `SystemExit: 2` escapes `main` (argparse default).

- [ ] **Step 3: Modify `core/mneme_index/cli.py`**

Add near the top:

```python
class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"mneme-index: {message}", file=sys.stderr)
        raise _UsageError(message)
```

In `_build_parser`, construct the root parser as `_Parser(prog="mneme-index")` (sub-parsers inherit the class automatically). In `main`, wrap parsing:

```python
def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except _UsageError:
        return 1
    ...
```

(the rest of `main` unchanged).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/index/test_index_cli_errors.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_index/cli.py tests/index/test_index_cli_errors.py
git commit -m "fix: mneme-index usage errors exit 1"
```

---

### Task 6: `summary` field — schema v2

**Files:**
- Modify: `core/mneme_index/db.py`, `core/mneme_index/build.py`, `core/mneme_index/search.py`, `tests/index/test_search.py` (hit-key contract only)
- Create: `tests/index/test_summary_field.py`

**Interfaces:**
- Consumes: everything from Plan 02 + Tasks 2–3.
- Produces: spec §6.1's "body summary" as a first-class indexed field.
  - `db.SCHEMA_VERSION = "2"`; `units` gains `summary TEXT NOT NULL DEFAULT ''` after `description`; `units_fts` columns become `(plugin UNINDEXED, id UNINDEXED, name, description, summary, tags)`. Existing v1 DBs hit the version guard ("delete the database and rebuild") — that is the intended migration path for derived state.
  - `build.py`: canonical row becomes the 12-tuple `(plugin, id, kind, name, description, summary, category, tags, path, line, verified, hash)`; dedupe/count logic updates its indices (`path` is now index 8, `line` index 9, `kind` still index 2); skills get `summary = _summarize(body)` (whitespace-normalized body, first 400 chars); facts get `summary = ""`. FTS insert uses indices `(0, 1, 3, 4, 5, 7)`.
  - `search.py`: `search()` selects `u.summary` (hit dicts gain a `"summary"` key) and matches against the new FTS column automatically.
  - `tests/index/test_search.py`: the exact-key-set assertion adds `"summary"` — a deliberate contract change, nothing else in that file may change.

- [ ] **Step 1: Write the failing tests**

Create `tests/index/test_summary_field.py`:

```python
from mneme_index import build, db, search


def make_tree(root):
    d = root / "skills" / "deploy-widget"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: deploy-widget\ndescription: Use when deploying the widget service\n---\n"
        "Run the preflight checklist, then execute the blue-green cutover procedure.\n",
        encoding="utf-8",
    )
    facts = root / "facts"
    facts.mkdir()
    (facts / "t.md").write_text(
        "---\ntopic: t\n---\n- [gotcha] Plain fact #x (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    return root


def test_schema_version_is_2():
    assert db.SCHEMA_VERSION == "2"


def test_skill_summary_extracted_and_searchable(tmp_path):
    conn = db.open_db(tmp_path / "i.db")
    build.index_tree(conn, "p", make_tree(tmp_path / "tree"))
    row = conn.execute("SELECT summary FROM units WHERE kind = 'skill'").fetchone()
    assert "blue-green cutover" in row["summary"]
    hits = search.search(conn, "cutover preflight")
    assert any(h["id"] == "skills/deploy-widget" for h in hits)
    assert "summary" in hits[0]
    conn.close()


def test_fact_summary_empty(tmp_path):
    conn = db.open_db(tmp_path / "i.db")
    build.index_tree(conn, "p", make_tree(tmp_path / "tree"))
    row = conn.execute("SELECT summary FROM units WHERE kind = 'fact'").fetchone()
    assert row["summary"] == ""
    conn.close()


def test_summary_capped_at_400(tmp_path):
    root = tmp_path / "tree"
    d = root / "skills" / "long-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: long-skill\ndescription: d\n---\n" + ("word " * 300),
        encoding="utf-8",
    )
    conn = db.open_db(tmp_path / "i.db")
    build.index_tree(conn, "p", root)
    row = conn.execute("SELECT summary FROM units WHERE kind = 'skill'").fetchone()
    assert len(row["summary"]) <= 400
    conn.close()
```

Modify `tests/index/test_search.py` — in `test_search_finds_fact_by_vague_words`, extend the exact key set:

```python
    assert set(hit) == {
        "plugin", "id", "kind", "name", "description", "category",
        "tags", "path", "line", "verified", "score", "summary",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/index/test_summary_field.py tests/index/test_search.py -v`
Expected: summary tests FAIL (`no such column: summary`, version "1"); the key-set test FAILS (no `summary` key).

- [ ] **Step 3: Implement across the three modules**

`core/mneme_index/db.py` — set `SCHEMA_VERSION = "2"`; in `_SCHEMA`, add `summary TEXT NOT NULL DEFAULT ''` between `description` and `category` in `units`; in `_FTS`, columns become `plugin UNINDEXED, id UNINDEXED, name, description, summary, tags`. If the audit-fix added a required-tables/validation helper, keep it in sync.

`core/mneme_index/build.py` — add:

```python
def _summarize(body: str) -> str:
    return " ".join(body.split())[:400]
```

In `_skill_rows`, insert `_summarize(_body)` into the row after `description` (rename the unused `_body` binding to `body` where needed); in `_fact_rows`, insert `""` after `bullet.text`. Update `index_tree`: the INSERT gains the `summary` column (12 placeholders), the FTS INSERT becomes columns `(plugin, id, name, description, summary, tags)` from row indices `(0, 1, 3, 4, 5, 7)`, and the dedupe/skip line-reference indices shift (`path` = `r[8]`, `line` = `r[9]`).

`core/mneme_index/search.py` — in `search()`, add `u.summary` to the SELECT list (after `u.description`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/index -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS (if an audit-fix regression test asserts a literal schema version or column list, update it minimally to the v2 contract and record the deviation).

```bash
git add core/mneme_index/db.py core/mneme_index/build.py core/mneme_index/search.py tests/index/test_summary_field.py tests/index/test_search.py
git commit -m "feat: summary column and FTS field, schema v2"
```

---

### Task 7: `mneme db enable` / `mneme db disable`

**Files:**
- Modify: `core/mneme_core/cli.py`
- Create: `tests/core/test_db_enable.py`

**Interfaces:**
- Consumes: `indexing.rebuild`, `mneme_index.db.open_db`, `paths.db_path`.
- Produces: `mneme db enable` — creates the DB and populates it (runs `indexing.rebuild` when the registry is non-empty; with an empty registry it creates an empty schema DB), prints `index enabled at <path>`; `mneme db disable` — deletes the DB file if present (idempotent), prints `index disabled`. Spec §6's off-by-default gate is the DB file itself: index-dependent commands already degrade gracefully when it's absent.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_db_enable.py`:

```python
from mneme_core import paths, registry
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_enable_with_empty_registry_creates_empty_db(tmp_path, capsys):
    home = tmp_path / "home"
    code, out, _ = run(capsys, "--home", str(home), "db", "enable")
    assert code == 0
    assert "index enabled" in out
    assert paths.db_path(home).exists()


def test_enable_with_registry_populates(tmp_path, capsys):
    home = tmp_path / "home"
    tree = tmp_path / "clone"
    d = tree / "skills" / "a-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: a-skill\ndescription: d\n---\nBody\n", encoding="utf-8"
    )
    registry.add_plugin(home, Plugin(name="p-one", repo="r", path=str(tree)))
    code, out, _ = run(capsys, "--home", str(home), "db", "enable")
    assert code == 0
    assert "indexed p-one: 1 skills" in out
    code, out, _ = run(capsys, "--home", str(home), "search", "d")
    assert code == 0


def test_disable_removes_db_and_is_idempotent(tmp_path, capsys):
    home = tmp_path / "home"
    run(capsys, "--home", str(home), "db", "enable")
    assert paths.db_path(home).exists()
    code, out, _ = run(capsys, "--home", str(home), "db", "disable")
    assert code == 0
    assert "index disabled" in out
    assert not paths.db_path(home).exists()
    code, _, _ = run(capsys, "--home", str(home), "db", "disable")
    assert code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_db_enable.py -v`
Expected: FAIL — argparse usage errors (`invalid choice: 'enable'`).

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

In `_build_parser`, extend the `db` subparser group:

```python
    db_sub.add_parser("enable")
    db_sub.add_parser("disable")
```

In `_db_cmd`, dispatch on `args.db_command` before the query path:

```python
def _db_cmd(home: Path, args: argparse.Namespace) -> int:
    if args.db_command == "enable":
        from mneme_index import db as index_db

        from . import indexing, registry as registry_mod

        paths.ensure_layout(home)
        if registry_mod.load_registry(home):
            for s in indexing.rebuild(home):
                print(
                    f"indexed {s.plugin}: {s.skills} skills,"
                    f" {s.facts} facts, {len(s.skipped)} skipped"
                )
        else:
            index_db.open_db(paths.db_path(home)).close()
        print(f"index enabled at {paths.db_path(home)}")
        return 0
    if args.db_command == "disable":
        db_file = paths.db_path(home)
        if db_file.exists():
            db_file.unlink()
        print("index disabled")
        return 0
    # query path continues below (Task 1's SELECT-only + authorizer logic)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_db_enable.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_db_enable.py
git commit -m "feat: mneme db enable/disable"
```

---

### Task 8: Knowledge-plugin templates (`templates.py`)

**Files:**
- Create: `core/mneme_core/templates.py`, `tests/core/test_templates.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `render(template: str, **subs) -> str` (strict `string.Template.substitute`) and these constants, each a `string.Template`-style string with `$name`, `$description`, `$owner`, `$sensitivity`, `$mode` placeholders as noted: `PLUGIN_JSON`, `MARKETPLACE_JSON`, `MNEME_MD`, `AGENTS_MD`, `README_MD`, `CONTRIBUTING_MD`, `CODEOWNERS`, `VALIDATE_YML` (no placeholders), `RELEASE_YML` (no placeholders), `GITIGNORE` (no placeholders), `INDEX_SKILL_MD` (`$name`, `$description`).

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_templates.py`:

```python
import json

from mneme_core import templates


SUBS = dict(
    name="acme-knowledge",
    description="Institutional knowledge for the Acme widget platform",
    owner="acme-maintainers",
    sensitivity="internal",
    mode="pr",
)


def test_plugin_json_renders_valid_json():
    data = json.loads(templates.render(templates.PLUGIN_JSON, **SUBS))
    assert data["name"] == "acme-knowledge"
    assert data["version"] == "0.1.0"
    assert data["description"] == SUBS["description"]


def test_marketplace_json_renders_valid_json():
    data = json.loads(templates.render(templates.MARKETPLACE_JSON, **SUBS))
    assert data["name"] == "acme-knowledge"
    assert data["owner"]["name"] == "acme-maintainers"
    assert data["plugins"][0]["source"] == "./"


def test_mneme_md_carries_scope_and_sensitivity():
    text = templates.render(templates.MNEME_MD, **SUBS)
    assert "## Scope statement" in text
    assert "internal" in text
    assert SUBS["description"] in text
    assert "## What does NOT belong here" in text


def test_codeowners_has_owner():
    text = templates.render(templates.CODEOWNERS, **SUBS)
    assert "* @acme-maintainers" in text


def test_contributing_has_rubric_and_ai_policy():
    text = templates.render(templates.CONTRIBUTING_MD, **SUBS)
    assert "verified" in text.lower()
    assert "unreviewed" in text.lower()


def test_workflows_reference_mneme_tooling():
    assert "bin/mneme lint" in templates.VALIDATE_YML
    assert "plugin.json" in templates.RELEASE_YML


def test_index_skill_renders_lintable_frontmatter():
    text = templates.render(templates.INDEX_SKILL_MD, **SUBS)
    assert text.startswith("---\nname: knowledge-index\n")
    assert "description:" in text


def test_render_rejects_missing_substitution():
    import pytest

    with pytest.raises(KeyError):
        templates.render(templates.PLUGIN_JSON, name="only-name")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_templates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.templates'`.

- [ ] **Step 3: Implement `core/mneme_core/templates.py`**

```python
"""Knowledge-plugin scaffold templates (spec §5.1, §8). Pure data — no logic."""
from __future__ import annotations

from string import Template


def render(template: str, **subs: str) -> str:
    return Template(template).substitute(**subs)


PLUGIN_JSON = """{
  "name": "$name",
  "version": "0.1.0",
  "description": "$description"
}
"""

MARKETPLACE_JSON = """{
  "name": "$name",
  "owner": { "name": "$owner" },
  "plugins": [
    { "name": "$name", "source": "./", "description": "$description" }
  ]
}
"""

MNEME_MD = """# $name — knowledge scope

**Sensitivity:** $sensitivity
**Contribution mode:** $mode
**Maintainers:** $owner

## Scope statement

$description

## What belongs here

- Hard-won procedures (skills): verified fixes, deployment paths, debugging golden paths — each with the failure pattern that made it non-obvious.
- Durable facts: constraints, gotchas, decisions, runbook notes that stay true across tickets.

## What does NOT belong here

- One-off decisions tied to a single ticket or conversation.
- Secrets, credentials, tokens, or personal data — the capture pipeline blocks them, and so does CI.
- Anything derivable from public documentation.

## Routing

This scope statement is the routing prompt: mneme's distiller matches candidate knowledge
against it. Keep it specific — name the products, systems, and processes this plugin covers.
"""

AGENTS_MD = """# $name

Agent-facing knowledge plugin. Procedural knowledge lives in `skills/` (Agent Skills
format); durable facts live in `facts/` and are routed through the `knowledge-index`
skill. See `MNEME.md` for what belongs here and `CONTRIBUTING.md` for how knowledge
gets in.
"""

README_MD = """# $name

$description

A [mneme](https://github.com/rhoulihan/mneme) knowledge plugin: procedures as Agent
Skills in `skills/`, durable facts in `facts/`, governance in CI. Install it through
your agent's plugin marketplace tooling and inherit every merged update.

- Scope and routing: `MNEME.md`
- Contribution pipeline: `CONTRIBUTING.md`
- Reviewers: `CODEOWNERS`
"""

CONTRIBUTING_MD = """# Contributing knowledge to $name

Knowledge enters this repo through pull requests — human-written or staged by mneme's
curated harvest. Either way the same rules apply.

## The promotion rule

A contribution must carry:

1. **Verified success** — the procedure or fact was actually exercised, not assumed.
2. **A named failure pattern** — what went wrong before the fix; the dead ends eliminated.
3. **Non-obviousness** — not derivable from public documentation.

## Format

- Skills: `skills/<name>/SKILL.md`, kebab-case `name` matching the directory,
  trigger-rich `description` (it IS the retrieval surface), provenance in `metadata`.
- Facts: one topic per file in `facts/`, typed bullets
  (`decision | constraint | gotcha | runbook-note | reference`), tags, verified dates.
- Delta edits only — never regenerate whole files.

CI (`validate.yml`) lints format and scans for secrets, so review can focus on substance.

## Review policy

- CODEOWNERS routes each area to its maintainers.
- Unreviewed AI-generated bulk contributions are closed without merge; every PR needs a
  human who vouches for the promotion rule above.
- Merges bump the plugin version automatically — accepted knowledge ships immediately.
"""

CODEOWNERS = """# Default reviewers for all knowledge in this plugin.
# Add per-area rules above the fallback as the repo grows, e.g.:
#   /skills/deploy-*  @platform-team
* @$owner
"""

VALIDATE_YML = """name: validate
on:
  pull_request:
  push:
    branches: [main]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Fetch mneme engine
        run: git clone --depth 1 https://github.com/rhoulihan/mneme /tmp/mneme
      - name: Lint knowledge units
        run: /tmp/mneme/bin/mneme lint .
      - name: Secret scan
        run: |
          set -e
          rc=0
          while IFS= read -r -d '' f; do
            /tmp/mneme/bin/mneme scan "$$f" || rc=$$?
          done < <(find skills facts -name '*.md' -print0 2>/dev/null)
          exit $$rc
"""

RELEASE_YML = """name: release
on:
  push:
    branches: [main]
jobs:
  bump:
    if: "!contains(github.event.head_commit.message, 'chore: bump version')"
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - name: Bump plugin version
        run: |
          python3 - <<'PY'
          import json, pathlib
          p = pathlib.Path('.claude-plugin/plugin.json')
          data = json.loads(p.read_text())
          major, minor, patch = data['version'].split('.')
          data['version'] = f"{major}.{minor}.{int(patch) + 1}"
          p.write_text(json.dumps(data, indent=2) + "\\n")
          PY
      - name: Commit bump
        run: |
          git config user.name "mneme-bot"
          git config user.email "mneme-bot@users.noreply.github.com"
          git commit -am "chore: bump version"
          git push
"""

GITIGNORE = """.DS_Store
Thumbs.db
__pycache__/
"""

INDEX_SKILL_MD = """---
name: knowledge-index
description: Consult when you need durable facts from $name — constraints, gotchas, decisions, and runbook notes. $description Topics listed in this skill route to fact files under facts/.
---

# $name fact index

Regenerated mechanically by mneme — do not edit by hand.

| Topic | File | Bullets |
|---|---|---|
"""
```

Note the `$$` escapes inside `VALIDATE_YML` — `string.Template` requires them for literal `$` in the shell snippet.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_templates.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/templates.py tests/core/test_templates.py
git commit -m "feat: knowledge-plugin templates"
```

---

### Task 9: Scaffold factory (`scaffold.py` — `create()`)

**Files:**
- Create: `core/mneme_core/scaffold.py`, `tests/core/test_scaffold.py`

**Interfaces:**
- Consumes: `templates`, `lint.lint_repo`, `lint.has_errors`, `registry` (`Plugin`, `add_plugin`), `paths.repos_dir`, `units.KEBAB_RE`, `MnemeError`, `subprocess`.
- Produces: `create(home: Path, name: str, *, directory: Path | None = None, description: str = "", owner: str = "maintainers", repo_url: str = "", mode: str = "pr", sensitivity: str = "internal") -> Path` — validates the kebab name; target = `directory or paths.repos_dir(home) / name` (MnemeError if it exists); writes the full tree (`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `MNEME.md`, `AGENTS.md`, `README.md`, `CONTRIBUTING.md`, `CODEOWNERS`, `.github/workflows/validate.yml`, `.github/workflows/release.yml`, `.gitignore`, `skills/knowledge-index/SKILL.md`, empty `facts/` dir); description defaults to `f"Institutional knowledge maintained with mneme: {name}."`; asserts `lint_repo(target)` has no error-severity issues (MnemeError otherwise — internal invariant); `git init -b main` + `git add -A` + first commit (`chore: scaffold <name> knowledge plugin`) with explicit `-c user.name=mneme -c user.email=mneme@localhost`; registers the plugin (`repo = repo_url or f"local:{target}"`); returns the target path. Also exports `_git(target: Path, *args: str) -> None` (subprocess wrapper, `check=True`, raising `MnemeError` with stderr excerpt on failure) for Task 10's tests to reuse mental model — keep it module-private but stable.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_scaffold.py`:

```python
import json
import subprocess

import pytest

from mneme_core import lint, paths, registry, scaffold
from mneme_core.errors import MnemeError


def test_create_full_tree(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="acme-team")
    assert target == paths.repos_dir(home) / "acme-knowledge"
    for rel in (
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "MNEME.md",
        "AGENTS.md",
        "README.md",
        "CONTRIBUTING.md",
        "CODEOWNERS",
        ".github/workflows/validate.yml",
        ".github/workflows/release.yml",
        ".gitignore",
        "skills/knowledge-index/SKILL.md",
    ):
        assert (target / rel).exists(), rel
    assert (target / "facts").is_dir()
    data = json.loads((target / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert data["name"] == "acme-knowledge"
    assert data["version"] == "0.1.0"


def test_scaffold_lints_clean(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "clean-knowledge")
    issues = lint.lint_repo(target)
    assert not lint.has_errors(issues)


def test_git_initialized_with_one_commit(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "git-knowledge")
    log = subprocess.run(
        ["git", "-C", str(target), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert len(log.strip().splitlines()) == 1
    assert "scaffold git-knowledge" in log
    branch = subprocess.run(
        ["git", "-C", str(target), "branch", "--show-current"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert branch == "main"


def test_registered_in_registry(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "reg-knowledge", sensitivity="restricted", mode="commit")
    p = registry.get_plugin(home, "reg-knowledge")
    assert p is not None
    assert p.path == str(target)
    assert p.sensitivity == "restricted"
    assert p.mode == "commit"
    assert p.repo == f"local:{target}"


def test_existing_target_rejected(tmp_path):
    home = tmp_path / "home"
    scaffold.create(home, "dup-knowledge")
    with pytest.raises(MnemeError):
        scaffold.create(home, "dup-knowledge")


def test_bad_name_rejected(tmp_path):
    with pytest.raises(MnemeError):
        scaffold.create(tmp_path / "home", "Bad_Name")


def test_custom_directory(tmp_path):
    home = tmp_path / "home"
    custom = tmp_path / "elsewhere" / "kb"
    target = scaffold.create(home, "custom-knowledge", directory=custom)
    assert target == custom
    assert (custom / "MNEME.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_scaffold.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mneme_core.scaffold'`.

- [ ] **Step 3: Implement `core/mneme_core/scaffold.py`**

```python
"""Scaffold factory — generates governed knowledge-plugin repos (spec §5.1, §8)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import lint, paths, registry, templates
from .errors import MnemeError
from .units import KEBAB_RE


def _git(target: Path, *args: str) -> None:
    cmd = [
        "git",
        "-c", "user.name=mneme",
        "-c", "user.email=mneme@localhost",
        "-C", str(target),
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise MnemeError(f"git {' '.join(args)} failed: {result.stderr.strip()[:300]}")


def create(
    home: Path,
    name: str,
    *,
    directory: Path | None = None,
    description: str = "",
    owner: str = "maintainers",
    repo_url: str = "",
    mode: str = "pr",
    sensitivity: str = "internal",
) -> Path:
    if not KEBAB_RE.match(name):
        raise MnemeError(f"plugin name must be kebab-case: {name!r}")
    target = directory if directory is not None else paths.repos_dir(home) / name
    if target.exists():
        raise MnemeError(f"target already exists: {target}")
    if not description:
        description = f"Institutional knowledge maintained with mneme: {name}."
    subs = dict(
        name=name, description=description, owner=owner, sensitivity=sensitivity, mode=mode
    )

    files = {
        ".claude-plugin/plugin.json": templates.render(templates.PLUGIN_JSON, **subs),
        ".claude-plugin/marketplace.json": templates.render(templates.MARKETPLACE_JSON, **subs),
        "MNEME.md": templates.render(templates.MNEME_MD, **subs),
        "AGENTS.md": templates.render(templates.AGENTS_MD, **subs),
        "README.md": templates.render(templates.README_MD, **subs),
        "CONTRIBUTING.md": templates.render(templates.CONTRIBUTING_MD, **subs),
        "CODEOWNERS": templates.render(templates.CODEOWNERS, **subs),
        ".github/workflows/validate.yml": templates.VALIDATE_YML,
        ".github/workflows/release.yml": templates.RELEASE_YML,
        ".gitignore": templates.GITIGNORE,
        "skills/knowledge-index/SKILL.md": templates.render(templates.INDEX_SKILL_MD, **subs),
    }
    for rel, content in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (target / "facts").mkdir(exist_ok=True)

    issues = lint.lint_repo(target)
    if lint.has_errors(issues):
        details = "; ".join(f"{i.code} {i.message}" for i in issues if i.severity == "error")
        raise MnemeError(f"scaffold generated a repo that fails lint (bug): {details}")

    _git(target, "init", "-b", "main")
    _git(target, "add", "-A")
    _git(target, "commit", "-m", f"chore: scaffold {name} knowledge plugin")

    registry.add_plugin(
        home,
        registry.Plugin(
            name=name,
            repo=repo_url or f"local:{target}",
            path=str(target),
            mode=mode,
            sensitivity=sensitivity,
        ),
    )
    return target
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_scaffold.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/scaffold.py tests/core/test_scaffold.py
git commit -m "feat: scaffold factory create()"
```

---

### Task 10: `knowledge-index` regeneration (`scaffold.py`, part 2)

**Files:**
- Modify: `core/mneme_core/scaffold.py` (append `regenerate_index_skill`; call it at the end of `create()` before the git commit)
- Create: `tests/core/test_regenerate.py`

**Interfaces:**
- Consumes: `units.parse_frontmatter`, `units.parse_bullet_line`, `templates.INDEX_SKILL_MD`, `templates.render`.
- Produces: `regenerate_index_skill(target: Path, name: str, description: str) -> Path` — deterministically rewrites `skills/knowledge-index/SKILL.md`: renders the template, then appends one table row per `facts/*.md` file (`| <topic> | facts/<file> | <bullet count> |`, sorted by filename; topic from frontmatter else stem; unparseable files counted as 0 bullets and still listed); the frontmatter `description` is the template's rendered description plus `" Topics: t1, t2, …"` when topics exist, hard-capped at 1024 chars (truncate at the cap; never emit an over-long description — it must stay lint-clean). This is mechanical regeneration of a generated artifact, the one sanctioned whole-file rewrite (spec §5.3). `create()` calls it after writing files so the initial commit contains the regenerated form.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_regenerate.py`:

```python
from mneme_core import lint, scaffold


def test_regenerate_reflects_fact_topics(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "regen-knowledge")
    (target / "facts" / "staging-env.md").write_text(
        "---\ntopic: staging-env\n---\n"
        "- [constraint] DB resets nightly #db (verified: 2026-08-11)\n"
        "- [gotcha] API truncates batches #api (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    (target / "facts" / "billing.md").write_text(
        "---\ntopic: billing\n---\n"
        "- [decision] Invoices settle monthly #billing (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    scaffold.regenerate_index_skill(
        target, "regen-knowledge", "Knowledge for the regen test."
    )
    text = (target / "skills" / "knowledge-index" / "SKILL.md").read_text(encoding="utf-8")
    assert "| billing | facts/billing.md | 1 |" in text
    assert "| staging-env | facts/staging-env.md | 2 |" in text
    assert text.index("billing |") < text.index("staging-env |")
    assert "Topics: billing, staging-env" in text


def test_regenerated_skill_stays_lint_clean(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "lint-knowledge")
    for i in range(40):
        (target / "facts" / f"topic-{i:02d}.md").write_text(
            f"---\ntopic: topic-{i:02d}\n---\n"
            f"- [reference] Reference number {i} #ref (verified: 2026-08-11)\n",
            encoding="utf-8",
        )
    scaffold.regenerate_index_skill(target, "lint-knowledge", "Many topics." )
    issues = lint.lint_repo(target)
    assert not lint.has_errors(issues)


def test_create_ships_regenerated_index(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "fresh-knowledge")
    text = (target / "skills" / "knowledge-index" / "SKILL.md").read_text(encoding="utf-8")
    assert "| Topic | File | Bullets |" in text


def test_unparseable_fact_file_listed_with_zero(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "tolerant-knowledge")
    (target / "facts" / "broken.md").write_text(
        "---\ntopic: broken\nno closing delim", encoding="utf-8"
    )
    scaffold.regenerate_index_skill(target, "tolerant-knowledge", "d")
    text = (target / "skills" / "knowledge-index" / "SKILL.md").read_text(encoding="utf-8")
    assert "| broken | facts/broken.md | 0 |" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_regenerate.py -v`
Expected: FAIL — `AttributeError: module 'mneme_core.scaffold' has no attribute 'regenerate_index_skill'`.

- [ ] **Step 3: Append to `core/mneme_core/scaffold.py`**

```python
from .errors import MnemeError  # (already imported at top — shown for context only)
from . import units


def regenerate_index_skill(target: Path, name: str, description: str) -> Path:
    facts_dir = target / "facts"
    entries: list[tuple[str, str, int]] = []
    if facts_dir.is_dir():
        for f in sorted(facts_dir.glob("*.md")):
            topic = f.stem
            count = 0
            try:
                meta, body = units.parse_frontmatter(f.read_text(encoding="utf-8-sig"))
                topic = str(meta.get("topic", f.stem))
                for n, line in enumerate(body.splitlines(), start=1):
                    if line.startswith("- ["):
                        try:
                            units.parse_bullet_line(line, n)
                            count += 1
                        except MnemeError:
                            continue
            except MnemeError:
                pass
            entries.append((topic, f"facts/{f.name}", count))

    text = templates.render(
        templates.INDEX_SKILL_MD, name=name, description=description,
        owner="", sensitivity="", mode="",
    )
    meta, body = units.parse_frontmatter(text)
    if entries:
        topic_list = ", ".join(t for t, _, _ in entries)
        meta["description"] = (str(meta["description"]) + f" Topics: {topic_list}")[:1024]
    rows = "".join(f"| {t} | {p} | {c} |\n" for t, p, c in entries)
    out = units.serialize_frontmatter(meta, body + rows)
    path = target / "skills" / "knowledge-index" / "SKILL.md"
    path.write_text(out, encoding="utf-8")
    return path
```

In `create()`, after the `files` loop and `facts` mkdir but **before** the lint check, add:

```python
    regenerate_index_skill(target, name, description)
```

(Adjust the top-of-file imports: `units` joins the existing `from . import …` list; do not duplicate the `MnemeError` import.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_regenerate.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/scaffold.py tests/core/test_regenerate.py
git commit -m "feat: knowledge-index skill regeneration"
```

---

### Task 11: `mneme new` command

**Files:**
- Modify: `core/mneme_core/cli.py`
- Create: `tests/core/test_cli_new.py`

**Interfaces:**
- Consumes: `scaffold.create`.
- Produces: `mneme new NAME [--dir PATH] [--description D] [--owner O] [--repo URL] [--mode pr|commit] [--sensitivity S]` → runs `scaffold.create`, prints `created <path>` and `registered <name>`. Errors surface through the standard `mneme: <msg>` exit-1 path.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_cli_new.py`:

```python
from mneme_core import registry
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_new_creates_and_registers(tmp_path, capsys):
    home = tmp_path / "home"
    code, out, _ = run(capsys, "--home", str(home), "new", "acme-knowledge", "--owner", "acme")
    assert code == 0
    assert "created" in out and "registered acme-knowledge" in out
    assert registry.get_plugin(home, "acme-knowledge") is not None


def test_new_then_lint_and_index(tmp_path, capsys):
    home = tmp_path / "home"
    run(capsys, "--home", str(home), "new", "flow-knowledge")
    p = registry.get_plugin(home, "flow-knowledge")
    code, _, _ = run(capsys, "lint", p.path)
    assert code == 0
    code, out, _ = run(capsys, "--home", str(home), "index", "rebuild")
    assert code == 0
    assert "indexed flow-knowledge: 1 skills, 0 facts" in out


def test_new_duplicate_errors(tmp_path, capsys):
    home = tmp_path / "home"
    run(capsys, "--home", str(home), "new", "dup-knowledge")
    code, _, err = run(capsys, "--home", str(home), "new", "dup-knowledge")
    assert code == 1
    assert "mneme:" in err


def test_new_custom_dir(tmp_path, capsys):
    home = tmp_path / "home"
    custom = tmp_path / "kb"
    code, out, _ = run(
        capsys, "--home", str(home), "new", "dir-knowledge", "--dir", str(custom)
    )
    assert code == 0
    assert str(custom) in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/core/test_cli_new.py -v`
Expected: FAIL — argparse `invalid choice: 'new'` usage errors.

- [ ] **Step 3: Modify `core/mneme_core/cli.py`**

In `_build_parser`:

```python
    p_new = sub.add_parser("new")
    p_new.add_argument("name")
    p_new.add_argument("--dir", type=Path, default=None)
    p_new.add_argument("--description", default="")
    p_new.add_argument("--owner", default="maintainers")
    p_new.add_argument("--repo", default="")
    p_new.add_argument("--mode", default="pr", choices=sorted(registry.MODES))
    p_new.add_argument("--sensitivity", default="internal", choices=sorted(registry.SENSITIVITIES))
```

In the dispatch:

```python
        if args.command == "new":
            from . import scaffold

            target = scaffold.create(
                home,
                args.name,
                directory=args.dir,
                description=args.description,
                owner=args.owner,
                repo_url=args.repo,
                mode=args.mode,
                sensitivity=args.sensitivity,
            )
            print(f"created {target}")
            print(f"registered {args.name}")
            return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/core/test_cli_new.py -v` → all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m pytest` → all PASS.

```bash
git add core/mneme_core/cli.py tests/core/test_cli_new.py
git commit -m "feat: mneme new command"
```

---

## Verification (end of plan)

1. `python3 -m pytest -v` — entire suite green (149 prior + this plan's new tests).
2. Hardening spot-checks:
   ```bash
   export MNEME_HOME=$(mktemp -d)
   bin/mneme init
   bin/mneme new demo-knowledge --owner demo-team
   bin/mneme db enable                                   # indexed demo-knowledge: 1 skills, 0 facts …
   bin/mneme db query "ATTACH DATABASE '/tmp/evil.db' AS evil"   # mneme: only SELECT queries are allowed, exit 1
   test ! -f /tmp/evil.db
   bin/mneme-index --db "$MNEME_HOME/mneme.db" frobnicate        # mneme-index: …, exit 1 (not 2)
   bin/mneme db disable && bin/mneme search x                     # mneme: index not built …, exit 1
   ```
3. Scaffold end-to-end:
   ```bash
   KB=$(bin/mneme registry list | awk '{print $1}' | head -1)   # demo-knowledge
   P=$(python3 -c "import json;print(json.load(open('$MNEME_HOME/registry.json'))['plugins'][0]['path'])")
   git -C "$P" log --oneline          # exactly one scaffold commit
   bin/mneme lint "$P"                # exit 0
   cat "$P/skills/knowledge-index/SKILL.md"   # header table present
   printf -- '---\ntopic: demo\n---\n- [gotcha] Demo fact #demo (verified: 2026-08-11)\n' > "$P/facts/demo.md"
   bin/mneme db enable                # re-index picks up 1 fact
   bin/mneme search "demo fact"       # fact hit
   ```
4. `git log --oneline` on the mneme repo shows one commit per task (11 new commits).

## Out of scope for Plan 03 (later plans)

- Routing + distiller (Plan 04), harvest/PR plumbing (Plan 05), Claude Code adapter + behavioral skills (Plan 06), e2e + dogfood repo (Plan 07).
- Vector layer, Oracle/Postgres drivers, Codex adapter — unchanged from the spec's deferred list.
- `.codex-plugin/` packaging in the scaffold: spec §5.1's tree anticipates it, but its manifest format is Codex-adapter (v1.1) territory — scaffolding a speculative stub now would be guesswork. The scaffold gains it alongside the Codex adapter. This is a deliberate deferral, not a gap.
