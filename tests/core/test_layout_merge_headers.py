"""A merge may never make a canonical fact file less readable than it was.

The `new_block` branch of `layout._merge` creates a frontmatter block on a canonical file
that had none, out of the legacy file's header. `_carry_meta` — the branch used when the
canonical file already has a header — routes lines the frontmatter grammar cannot key
into the body precisely so a stray legacy line can never make the canonical header
unparseable. `new_block` skipped that step, so a malformed legacy header was grafted in
whole: bytes and history survived, but `units.parse_frontmatter` then raised on the
merged file and every reader that walks it (lint, the index build, search, the classify
bundle) lost bullets that had been retrievable a moment earlier.

Retrievability is the property the repo exists for, so these tests assert it directly:
the merged file parses, and the index still yields a row for the bullet that had one.
"""
import subprocess

from mneme_core import layout, units
from mneme_index import build, db

CANON = units.FACTS_CANONICAL


def git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(repo), *args],
        check=True, capture_output=True,
    )


def make_repo(tmp_path):
    repo = tmp_path / "kb"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "README.md").write_text("kb\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "seed")
    return repo


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def indexed_fact_rows(tmp_path, repo, name):
    conn = db.open_db(tmp_path / f"{name}.db")
    try:
        build.index_tree(conn, name, repo)
        return conn.execute("SELECT COUNT(*) AS n FROM units WHERE kind = 'fact'").fetchone()["n"]
    finally:
        conn.close()


CANONICAL_BULLET = "- [gotcha] Canonical bullet that was already retrievable #x (verified: 2026-08-12)\n"
LEGACY_BULLET = "- [constraint] Legacy bullet arriving in the merge #y (verified: 2026-08-12)\n"


def _seed(tmp_path, legacy_header, before):
    """Canonical file with no header (one indexed bullet) + legacy file with `legacy_header`.

    `before` is the row count the index yields BEFORE migrating: 1 when the legacy header
    is malformed (that file was already unreadable — which is exactly why grafting its
    header into the canonical file was so damaging), 2 when it is well formed.
    """
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", CANONICAL_BULLET)
    write(repo / "facts" / "t.md", legacy_header + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")
    assert indexed_fact_rows(tmp_path, repo, "before") == before
    return repo


def test_an_unkeyable_legacy_header_line_never_breaks_the_canonical_header(tmp_path):
    repo = _seed(tmp_path, "---\nnot a key line\ntopic: t\n---\n", before=1)

    layout.migrate_legacy_facts(repo)

    merged = (repo / CANON / "t.md").read_text(encoding="utf-8")
    meta, body = units.parse_frontmatter(merged)  # must not raise
    assert meta.get("topic") == "t"
    assert "not a key line" in merged  # carried, never deleted
    assert "not a key line" in body    # in the body, not the header
    assert indexed_fact_rows(tmp_path, repo, "after") == 2


def test_an_indented_legacy_header_line_never_breaks_the_canonical_header(tmp_path):
    repo = _seed(tmp_path, "---\n  indented: yes\ntopic: t\n---\n", before=1)

    layout.migrate_legacy_facts(repo)

    merged = (repo / CANON / "t.md").read_text(encoding="utf-8")
    meta, body = units.parse_frontmatter(merged)  # must not raise
    assert meta.get("topic") == "t"
    assert "indented: yes" in merged
    assert indexed_fact_rows(tmp_path, repo, "after") == 2


def test_a_legacy_header_with_no_keyable_line_travels_with_the_body(tmp_path):
    repo = _seed(tmp_path, "---\nnot a key line\nalso not one\n---\n", before=1)

    layout.migrate_legacy_facts(repo)

    merged = (repo / CANON / "t.md").read_text(encoding="utf-8")
    units.parse_frontmatter(merged)  # must not raise
    assert "not a key line" in merged and "also not one" in merged
    assert indexed_fact_rows(tmp_path, repo, "after") == 2


def test_a_well_formed_legacy_header_still_becomes_the_block(tmp_path):
    repo = _seed(tmp_path, "---\ntopic: t\nowner: platform\n---\n", before=2)

    layout.migrate_legacy_facts(repo)

    meta, _body = units.parse_frontmatter((repo / CANON / "t.md").read_text(encoding="utf-8"))
    assert meta.get("topic") == "t"
    assert meta.get("owner") == "platform"
    assert indexed_fact_rows(tmp_path, repo, "after") == 2


def test_a_dropped_frontmatter_value_is_named_in_the_note(tmp_path):
    """Both values are knowledge; the note must say which one lost, not just which key."""
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", "---\ntopic: t\nowner: platform\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / "t.md", "---\ntopic: t\nowner: sre-oncall-team\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    result = layout.migrate_legacy_facts(repo)

    notes = " ".join(result.merged)
    assert "owner" in notes
    assert "sre-oncall-team" in notes  # the discarded value is recoverable from the report
    assert "platform" in notes
