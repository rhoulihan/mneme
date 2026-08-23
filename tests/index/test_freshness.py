"""The index must not answer confidently from a corpus it knows is out of date.

It is built from the WORKING TREES of registered repos, and nearly every event that changes
one belongs to somebody else — a maintainer merging a knowledge pull request most of all,
since PR-only means a human always does the merging. mneme is not running then, so nothing
marks the index dirty, and `mneme search` goes on answering from the old shape. The agent
then concludes the organisation does not know something it merged last week, which is the
exact failure mneme exists to prevent. It happened for real: after PR #4 merged into
oracle-ai-dev the index held the pre-merge shape until someone rebuilt by hand.

The read path stays read-only (`cli._require_index_db` opens the database with
`open_db_readonly` behind an authorizer that denies ATTACH/PRAGMA), so search DETECTS and
REPORTS rather than rebuilding. Turning a silent wrong answer into a loud stale one is the
win; rebuilding on read would only hide the staleness faster, on the hot path, without the
lock that would make it safe.
"""
import subprocess

import pytest

from mneme_core import gitops, indexing, registry, scaffold, units
from mneme_core.cli import main
from mneme_core.registry import Plugin

FACT = "---\ntopic: {t}\n---\n- [gotcha] {text} #x (verified: 2026-08-23)\n"


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def kb(tmp_path, home, name="team-kb"):
    target = scaffold.create(home, name, owner="demo", directory=tmp_path / name)
    write_fact(target, "deploys", "The drain window is ninety seconds")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "a fact")
    return target


def write_fact(target, topic, text):
    p = target / units.FACTS_CANONICAL / f"{topic}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FACT.format(t=topic, text=text), encoding="utf-8")
    return p


# --- the fingerprint ---------------------------------------------------------


def test_a_fingerprint_is_stable_when_nothing_changed(tmp_path):
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    assert indexing.fingerprint(target) == indexing.fingerprint(target)


def test_editing_a_fact_changes_it(tmp_path):
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    before = indexing.fingerprint(target)
    write_fact(target, "deploys", "The drain window is a hundred and twenty seconds")
    assert indexing.fingerprint(target) != before


def test_adding_and_removing_a_unit_changes_it(tmp_path):
    """The path LIST is part of the digest, so a new or deleted file counts even when no
    surviving file was touched."""
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    before = indexing.fingerprint(target)

    added = write_fact(target, "billing", "Invoices settle monthly")
    with_added = indexing.fingerprint(target)
    assert with_added != before

    added.unlink()
    assert indexing.fingerprint(target) == before


def test_an_edit_that_keeps_the_same_length_still_counts(tmp_path):
    """Size alone is not enough. "24 hours" -> "48 hours" is the ordinary correction, and
    it changes nothing a byte count can see — which is why mtime is in the digest."""
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    fact = write_fact(target, "deploys", "The drain window is 24 hours")
    before = indexing.fingerprint(target)

    corrected = "The drain window is 48 hours"
    write_fact(target, "deploys", corrected)
    assert fact.stat().st_size == len(FACT.format(t="deploys", text=corrected).encode())
    assert indexing.fingerprint(target) != before


def test_renaming_a_topic_counts(tmp_path):
    """A rename preserves size and mtime, so only the PATH in the digest catches it — and
    the index would otherwise keep serving the old topic name forever."""
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    before = indexing.fingerprint(target)

    old = target / units.FACTS_CANONICAL / "deploys.md"
    new_path = old.with_name("deployments.md")
    old.rename(new_path)

    assert new_path.stat().st_size == len(FACT.format(
        t="deploys", text="The drain window is ninety seconds").encode())
    assert indexing.fingerprint(target) != before


def test_a_skill_counts_too(tmp_path):
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    before = indexing.fingerprint(target)
    d = target / "skills" / "deploy-widget"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: deploy-widget\ndescription: Use when deploying\n---\nBody\n",
        encoding="utf-8",
    )
    assert indexing.fingerprint(target) != before


def test_a_file_the_index_never_reads_does_not_count(tmp_path):
    """Fingerprinting the whole tree would make every unrelated commit look like a change."""
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    before = indexing.fingerprint(target)
    (target / "README.md").write_text("rewritten\n", encoding="utf-8")
    (target / "notes.txt").write_text("scratch\n", encoding="utf-8")
    assert indexing.fingerprint(target) == before


# --- staleness ---------------------------------------------------------------


def test_a_freshly_built_index_is_not_stale(tmp_path):
    home = tmp_path / "home"
    kb(tmp_path, home)
    indexing.rebuild(home)
    assert indexing.stale(home) == []


def test_an_edit_outside_mneme_makes_it_stale(tmp_path):
    """The real scenario: a human merges a PR, or pulls, and mneme was never involved."""
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    indexing.rebuild(home)

    write_fact(target, "chargebacks", "Webhooks replay for 72 hours")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "merged from a PR")

    stale = indexing.stale(home)
    assert [s.plugin for s in stale] == ["team-kb"]
    assert "changed" in stale[0].reason


def test_an_uncommitted_edit_makes_it_stale_too(tmp_path):
    """The case `git rev-parse HEAD` alone would miss — the build reads the working tree."""
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    indexing.rebuild(home)
    head_before = gitops.git(target, "rev-parse", "HEAD")

    write_fact(target, "deploys", "The drain window is actually two minutes")

    assert gitops.git(target, "rev-parse", "HEAD") == head_before
    assert [s.plugin for s in indexing.stale(home)] == ["team-kb"]


def test_a_repo_never_indexed_is_stale(tmp_path):
    home = tmp_path / "home"
    kb(tmp_path, home)
    indexing.rebuild(home)
    kb(tmp_path, home, name="other-kb")
    assert [s.plugin for s in indexing.stale(home)] == ["other-kb"]


def test_a_missing_clone_is_reported_not_crashed(tmp_path):
    home = tmp_path / "home"
    kb(tmp_path, home)
    indexing.rebuild(home)
    registry.add_plugin(home, Plugin(name="ghost-kb", repo="r", path=str(tmp_path / "nope")))

    stale = indexing.stale(home)
    assert [s.plugin for s in stale] == ["ghost-kb"]
    assert "clone" in stale[0].reason


def test_an_index_built_before_fingerprints_existed_reads_as_stale_once(tmp_path):
    home = tmp_path / "home"
    kb(tmp_path, home)
    indexing.rebuild(home)
    from mneme_index import db as index_db
    from mneme_core import paths

    conn = index_db.open_db(paths.db_path(home))
    conn.execute("UPDATE plugins SET fingerprint = ''")
    conn.commit()
    conn.close()

    assert [s.plugin for s in indexing.stale(home)] == ["team-kb"]
    indexing.rebuild(home, only_stale=True)
    assert indexing.stale(home) == []


# --- rebuilding only what moved ----------------------------------------------


def test_only_stale_rebuilds_just_the_repo_that_moved(tmp_path):
    home = tmp_path / "home"
    kb(tmp_path, home, name="a-kb")
    moved = kb(tmp_path, home, name="b-kb")
    indexing.rebuild(home)

    write_fact(moved, "extra", "Something new")
    stats = indexing.rebuild(home, only_stale=True)

    assert [s.plugin for s in stats] == ["b-kb"]
    assert indexing.stale(home) == []


def test_a_full_rebuild_still_does_everything(tmp_path):
    home = tmp_path / "home"
    kb(tmp_path, home, name="a-kb")
    kb(tmp_path, home, name="b-kb")
    assert sorted(s.plugin for s in indexing.rebuild(home)) == ["a-kb", "b-kb"]


# --- the surfaces ------------------------------------------------------------


def test_search_warns_on_stderr_and_leaves_stdout_alone(tmp_path, capsys):
    """Every existing caller parses stdout. The warning must not land in it."""
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    indexing.rebuild(home)

    _c, fresh_out, fresh_err = run(capsys, "--home", str(home), "search", "drain window")
    assert fresh_err == ""

    write_fact(target, "chargebacks", "Webhooks replay for 72 hours")
    code, stale_out, stale_err = run(capsys, "--home", str(home), "search", "drain window")

    assert code == 0
    assert stale_out == fresh_out, "stdout changed when the index went stale"
    assert "stale" in stale_err.lower() and "team-kb" in stale_err
    assert "mneme index rebuild" in stale_err


def test_index_check_exits_2_when_stale_and_0_when_fresh(tmp_path, capsys):
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    indexing.rebuild(home)
    code, out, _ = run(capsys, "--home", str(home), "index", "check")
    assert code == 0 and "fresh" in out.lower()

    write_fact(target, "chargebacks", "Webhooks replay for 72 hours")
    code, out, _ = run(capsys, "--home", str(home), "index", "check")
    assert code == 2, out
    assert "team-kb" in out


def test_index_status_names_which_repos_are_behind(tmp_path, capsys):
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    indexing.rebuild(home)
    write_fact(target, "chargebacks", "Webhooks replay for 72 hours")

    _c, out, _ = run(capsys, "--home", str(home), "index", "status")
    assert "team-kb" in out and "stale" in out.lower()


def test_the_rebuild_command_takes_stale(tmp_path, capsys):
    home = tmp_path / "home"
    kb(tmp_path, home, name="a-kb")
    moved = kb(tmp_path, home, name="b-kb")
    run(capsys, "--home", str(home), "index", "rebuild")
    write_fact(moved, "extra", "Something new")

    _c, out, _ = run(capsys, "--home", str(home), "index", "rebuild", "--stale")
    assert "b-kb" in out and "a-kb" not in out


def test_mneme_status_names_the_count(tmp_path, capsys):
    home = tmp_path / "home"
    target = kb(tmp_path, home)
    indexing.rebuild(home)
    write_fact(target, "chargebacks", "Webhooks replay for 72 hours")

    _c, out, _ = run(capsys, "--home", str(home), "status")
    assert "stale" in out.lower(), out


def test_a_database_that_predates_the_column_says_so_rather_than_guessing(tmp_path):
    """`open_db_readonly` cannot migrate — migrations need a writable connection — so the
    read path meets the old shape and must report the truth about it.

    Found by running against a real `~/.mneme` rather than a fixture: every repo came back
    "never indexed", which would send a user to rebuild repos that were probably fine.
    """
    import sqlite3

    from mneme_core import paths

    home = tmp_path / "home"
    kb(tmp_path, home)
    indexing.rebuild(home)
    assert indexing.stale(home) == []

    # Recreate the pre-fingerprint schema: same rows, no column.
    conn = sqlite3.connect(paths.db_path(home))
    conn.execute("ALTER TABLE plugins RENAME TO plugins_old")
    conn.execute(
        "CREATE TABLE plugins (name TEXT PRIMARY KEY, root TEXT NOT NULL,"
        " repo TEXT NOT NULL DEFAULT '', sensitivity TEXT NOT NULL DEFAULT '',"
        " built_at TEXT NOT NULL DEFAULT '')"
    )
    conn.execute(
        "INSERT INTO plugins SELECT name, root, repo, sensitivity, built_at FROM plugins_old"
    )
    conn.execute("DROP TABLE plugins_old")
    conn.commit()
    conn.close()

    stale = indexing.stale(home)
    assert [s.plugin for s in stale] == ["team-kb"]
    assert "before freshness tracking" in stale[0].reason
    assert "never indexed" not in stale[0].reason

    # And one rebuild settles it — the migration runs on the writable connection.
    indexing.rebuild(home, only_stale=True)
    assert indexing.stale(home) == []


def test_a_touch_that_changes_no_content_is_not_a_change(tmp_path):
    """`git checkout` and `git pull` rewrite mtimes without touching content. A
    timestamp-based signal would call every pull a change and burn a rebuild on nothing."""
    import os

    home = tmp_path / "home"
    target = kb(tmp_path, home)
    before = indexing.fingerprint(target)

    fact = target / units.FACTS_CANONICAL / "deploys.md"
    st = fact.stat()
    os.utime(fact, ns=(st.st_atime_ns + 10**9, st.st_mtime_ns + 10**9))

    assert fact.stat().st_mtime_ns != st.st_mtime_ns
    assert indexing.fingerprint(target) == before
