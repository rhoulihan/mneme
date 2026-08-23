"""What the freshness check does when it cannot see — the failures agreement hides.

An adversarial review made the point that broke the first design: proving the fingerprint's
file set equals the BUILD's file set proves only that the two track each other. It says
nothing about whether either tracks the REPO. `Path.glob` returns `[]` for a directory it
cannot read — it swallows the `PermissionError` that `os.scandir` raises — so an unreadable
facts directory looks empty to both sides at once. They agree, the invariant holds, the
rebuild reports `0 skipped`, `index check` exits 0 saying "fresh", and `search` prints
nothing on stdout and nothing on stderr while the repo's knowledge sits on disk.

Agreement between two observers blinded the same way is not evidence of correctness.

The rule these tests pin: **when the check cannot see, it says so.** Never "fresh".
"""
import os
import pathlib
import sqlite3


import pytest

from mneme_core import indexing, paths, registry, scaffold, units
from mneme_core.cli import main
from mneme_core.registry import Plugin
from mneme_index import db as index_db

from tests.index.test_freshness import kb, run, write_fact

REPO_CORE = pathlib.Path(__file__).resolve().parents[2] / "core"




class chmod_000:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.mode = self.path.stat().st_mode
        os.chmod(self.path, 0o000)
        return self.path

    def __exit__(self, *exc):
        os.chmod(self.path, self.mode)
        return False


# --- F1: blindness must not read as emptiness --------------------------------


def test_an_unreadable_facts_dir_is_never_reported_as_fresh(tmp_path):
    """The reproduced failure: chmod the directory, rebuild, and every surface says fine.

    The rebuild indexes zero facts and reports zero skipped — a clean success — and the
    stored fingerprint records that blindness, so the next check compares blind to blind
    and agrees.
    """
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    indexing.rebuild(home)

    with chmod_000(target / units.FACTS_CANONICAL):
        stats = indexing.rebuild(home)
        assert any(s.skipped for s in stats), "a rebuild that saw nothing claimed success"
        behind = indexing.stale(home)
        assert [s.plugin for s in behind] == ["team-kb"], "reported fresh while blind"
        assert "read" in behind[0].reason.lower()


def test_an_unreadable_dir_hashes_differently_from_an_empty_one(tmp_path):
    """The two must not collide, or the stored fingerprint bakes the blindness in."""
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    facts = target / units.FACTS_CANONICAL

    with chmod_000(facts):
        blind = indexing.fingerprint(target)
    for f in facts.glob("*.md"):
        f.unlink()
    empty = indexing.fingerprint(target)

    assert blind != empty


def test_listing_a_repo_that_cannot_be_read_never_raises(tmp_path):
    """`stale()` promises it never fails — `search` and `status` must still answer.

    The guard only covered a MISSING root, not an unreadable one, and the `except OSError`
    around the read did not cover the listing that comes before it.
    """
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    indexing.rebuild(home)

    with chmod_000(target / "skills"):
        behind = indexing.stale(home)  # must not raise
        assert [s.plugin for s in behind] == ["team-kb"]


def test_search_and_status_survive_an_unreadable_repo(tmp_path, capsys):
    home = tmp_path / "home"
    target = kb(tmp_path, home, name="a-kb")
    other = kb(tmp_path, home, name="b-kb")
    indexing.rebuild(home)

    with chmod_000(other / "skills"):
        code, out, err = run(capsys, "--home", str(home), "search", "drain window")
        assert code == 0, err
        assert "a-kb" in out, "hits from the readable repo were lost"
        assert "b-kb" in err

        code, out, _ = run(capsys, "--home", str(home), "status")
        assert code == 0
        assert "STALE" in out


# --- F2: not being able to tell is not "fresh" -------------------------------


def test_a_missing_index_is_not_fresh(tmp_path, capsys):
    home = tmp_path / "home"
    kb(tmp_path, home)

    behind = indexing.stale(home)
    assert [s.plugin for s in behind] == ["team-kb"]

    code, out, _ = run(capsys, "--home", str(home), "index", "check")
    assert code == 2, out


def test_rebuild_stale_converges_in_one_pass_on_a_fresh_install(tmp_path, capsys):
    """It took two: `stale()` returned [] with no database, so `--stale` indexed nothing
    and exited 0 with no output, and only the SECOND check reported anything."""
    home = tmp_path / "home"
    kb(tmp_path, home)

    code, out, _ = run(capsys, "--home", str(home), "index", "rebuild", "--stale")
    assert code == 0
    assert "team-kb" in out, out
    assert indexing.stale(home) == []


def test_a_corrupt_index_is_not_fresh(tmp_path, capsys):
    home = tmp_path / "home"
    kb(tmp_path, home)
    indexing.rebuild(home)
    paths.db_path(home).write_bytes(b"this is not a database")

    behind = indexing.stale(home)
    assert behind and all("read" in s.reason or "unusable" in s.reason for s in behind), behind
    code, _out, _ = run(capsys, "--home", str(home), "index", "check")
    assert code == 2


# --- F3: knowledge from a de-registered repo keeps answering -----------------


def test_a_de_registered_repo_still_in_the_index_is_reported(tmp_path, capsys):
    home = tmp_path / "home"
    kb(tmp_path, home, name="a-kb")
    kb(tmp_path, home, name="b-kb")
    indexing.rebuild(home)
    registry.remove_plugin(home, "b-kb")

    behind = indexing.stale(home)
    assert [s.plugin for s in behind] == ["b-kb"]
    assert "registered" in behind[0].reason


def test_removing_the_last_plugin_can_still_be_cleaned_up(tmp_path):
    """`rebuild` raised "no plugins registered" BEFORE pruning, so the orphaned corpus
    answered forever and only `db disable` could clear it."""
    home = tmp_path / "home"
    kb(tmp_path, home)
    indexing.rebuild(home)
    registry.remove_plugin(home, "team-kb")

    indexing.rebuild(home, only_stale=True)

    conn = index_db.open_db(paths.db_path(home))
    try:
        assert conn.execute("SELECT count(*) FROM units").fetchone()[0] == 0
    finally:
        conn.close()


# --- F5: a lock is not a diagnosis -------------------------------------------


def test_a_lock_is_not_reported_as_never_indexed(tmp_path):
    """`except sqlite3.OperationalError` also catches "database is locked", so a
    concurrent rebuild made a genuinely fresh index report "never indexed"."""
    home = tmp_path / "home"
    kb(tmp_path, home)
    indexing.rebuild(home)
    assert indexing.stale(home) == []

    holder = sqlite3.connect(paths.db_path(home), timeout=0.1)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        behind = indexing.stale(home)
    finally:
        holder.rollback()
        holder.close()

    for s in behind:
        assert "never indexed" not in s.reason, s.reason
        assert "predates" not in s.reason and "before freshness" not in s.reason, s.reason


def test_a_lock_that_clears_mid_check_is_not_diagnosed_as_an_old_schema(tmp_path):
    """The distinguishing interleave: the first SELECT is locked, the fallback succeeds.

    Both branches catch `OperationalError`, and under a HELD lock both end at "could not be
    read" — so a test that only holds the lock cannot tell them apart. Here the lock clears
    in between, which is what a concurrent rebuild finishing actually looks like, and the
    two branches diverge: "no such column" means an old schema, a lock means we know
    nothing at all.
    """
    home = tmp_path / "home"
    kb(tmp_path, home)
    indexing.rebuild(home)

    real_open = index_db.open_db_readonly

    class FlakyOnce:
        def __init__(self, conn):
            self._conn = conn
            self._first = True

        def execute(self, sql, *a, **kw):
            if self._first and "fingerprint" in sql:
                self._first = False
                raise sqlite3.OperationalError("database is locked")
            return self._conn.execute(sql, *a, **kw)

        def close(self):
            self._conn.close()

    import mneme_core.indexing as mod

    mod.index_db.open_db_readonly = lambda p: FlakyOnce(real_open(p))
    try:
        behind = indexing.stale(home)
    finally:
        mod.index_db.open_db_readonly = real_open

    assert behind, "a lock must not read as fresh"
    for s in behind:
        assert "before freshness tracking" not in s.reason, s.reason
        assert "could not be read" in s.reason or "unusable" in s.reason, s.reason


def test_a_large_fact_file_is_not_read_into_memory_whole(tmp_path):
    """`read_bytes` allocated a 300 MB file whole, on the agent's hot read path.

    Measured in a subprocess so the assertion is about peak RSS, not about a code shape a
    refactor could satisfy while reallocating.
    """
    import subprocess
    import sys
    import textwrap

    home = tmp_path / "home"
    target = kb(tmp_path, home)
    big = target / units.FACTS_CANONICAL / "huge.md"
    with big.open("wb") as f:
        f.write(b"---\ntopic: huge\n---\n")
        for _ in range(64):
            f.write(b"x" * (1 << 20))

    script = textwrap.dedent(f"""
        import resource, sys
        sys.path.insert(0, {str(REPO_CORE)!r})
        from mneme_core import indexing
        from pathlib import Path
        indexing.fingerprint(Path({str(target)!r}))
        print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    peak_mb = int(out.stdout.strip())
    assert peak_mb < 60, f"peak RSS {peak_mb} MB for a 64 MB file — the read is unbounded"
