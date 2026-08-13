"""A merge may never make a canonical fact file less readable than it was.

`layout._merge` folds a legacy fact file into a canonical one and then deletes the legacy
file, so every line it does not carry is gone, and every line it carries into the wrong
place can break the file for the reader. The failure that matters is not an exception —
it is a file whose bytes are all present and which `units.parse_frontmatter` then rejects,
because every consumer that walks facts (lint, the index build, search, the classify
bundle) reads through that parser. A bullet that was retrievable before the migration and
is not after it has been lost, whatever the bytes say.

Two rounds of fixes here failed by re-deriving the reader's grammar instead of asking it:
`_meta_blocks` recognises a key only at the start of a line and attaches every other line
to the PRECEDING key, so "unkeyable" lines were caught only in first position, and a stray
line one row lower still entered the header. These tests therefore assert the PROPERTY
over a table of header shapes rather than pinning the two strings that were reported:

  1. the merged file parses,
  2. every legacy line survives somewhere in it,
  3. the index yields no fewer fact rows than before the merge.
"""
import subprocess

import pytest

from mneme_core import layout, units
from mneme_index import build, db

CANON = units.FACTS_CANONICAL

CANONICAL_BULLET = "- [gotcha] Canonical bullet that was already retrievable #x (verified: 2026-08-12)\n"
LEGACY_BULLET = "- [constraint] Legacy bullet arriving in the merge #y (verified: 2026-08-12)\n"

# Every header shape the reviewer demonstrated, in both orders where order mattered.
MALFORMED_HEADERS = {
    "stray-line-first": "not a key line\ntopic: t\n",
    "stray-line-after-a-key": "topic: t\nnot a key line\n",
    "indented-line-first": "  indented: yes\ntopic: t\n",
    "indented-line-after-a-key": "topic: t\n  indented: yes\n",
    "flush-left-list": "tags:\n- a\n- b\n",
    "prose-tail": "topic: deploys\nowner: platform\nOwned by the platform team since 2024\n",
    "bad-nested-block": "owner:\n  not a nested key\n",
    "tab-continuation": "topic: t\n\tsub: v\n",
    "nothing-keyable-at-all": "not a key line\nalso not one\n",
}

WELL_FORMED_HEADER = "topic: t\nowner: platform\n"


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


def fact_rows(tmp_path, repo, name):
    """How many fact bullets the index can actually retrieve from `repo` right now."""
    conn = db.open_db(tmp_path / f"{name}.db")
    try:
        build.index_tree(conn, name, repo)
        return conn.execute(
            "SELECT COUNT(*) AS n FROM units WHERE kind = 'fact'"
        ).fetchone()["n"]
    finally:
        conn.close()


def assert_merge_preserved(tmp_path, repo, legacy_header, canonical_path):
    """The three properties, checked around a real migration."""
    before = fact_rows(tmp_path, repo, "before")
    legacy_lines = [l for l in legacy_header.splitlines() if l.strip()]

    layout.migrate_legacy_facts(repo)

    merged = canonical_path.read_text(encoding="utf-8")
    units.parse_frontmatter(merged)  # 1. the reader accepts what the merge produced
    for line in legacy_lines:  # 2. nothing the legacy file carried was dropped
        assert line.strip() in merged, f"legacy header line vanished: {line!r}"
    assert "Legacy bullet arriving in the merge" in merged
    after = fact_rows(tmp_path, repo, "after")
    assert after >= before, f"retrievable facts fell from {before} to {after}"
    assert after >= 1
    return merged


@pytest.mark.parametrize("shape", sorted(MALFORMED_HEADERS))
def test_a_malformed_legacy_header_never_breaks_a_canonical_file_without_one(tmp_path, shape):
    """The `new_block` branch: the canonical file has no header, so one is created."""
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", CANONICAL_BULLET)
    write(repo / "facts" / "t.md", f"---\n{MALFORMED_HEADERS[shape]}---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    assert_merge_preserved(tmp_path, repo, MALFORMED_HEADERS[shape], canonical)


@pytest.mark.parametrize("shape", sorted(MALFORMED_HEADERS))
def test_a_malformed_legacy_header_never_breaks_a_canonical_file_with_one(tmp_path, shape):
    """The `_carry_meta` branch: the canonical file already has a header to carry keys into."""
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", "---\nsummary: canonical\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / "t.md", f"---\n{MALFORMED_HEADERS[shape]}---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    merged = assert_merge_preserved(tmp_path, repo, MALFORMED_HEADERS[shape], canonical)
    meta, _body = units.parse_frontmatter(merged)
    assert meta.get("summary") == "canonical"  # the canonical header still reads


def test_a_well_formed_legacy_header_still_becomes_the_block(tmp_path):
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", CANONICAL_BULLET)
    write(repo / "facts" / "t.md", f"---\n{WELL_FORMED_HEADER}---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    merged = assert_merge_preserved(tmp_path, repo, WELL_FORMED_HEADER, canonical)
    meta, _body = units.parse_frontmatter(merged)
    assert meta.get("topic") == "t"
    assert meta.get("owner") == "platform"  # carried as metadata, not demoted to prose


def test_a_well_formed_legacy_key_still_reaches_an_existing_canonical_header(tmp_path):
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", "---\ntopic: t\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / "t.md", "---\ntopic: t\nowner: platform\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    layout.migrate_legacy_facts(repo)

    meta, _body = units.parse_frontmatter(canonical.read_text(encoding="utf-8"))
    assert meta.get("owner") == "platform"


def test_carried_body_is_never_read_as_a_frontmatter_block(tmp_path):
    """A legacy body opening with `---` must not become a header when it lands first.

    The legacy file's own leading blank line is what kept those lines out of its header;
    dropping it while carrying them into an empty canonical file turned prose into an
    unterminated block, and the delimiter-deduping dropped the closing `---` besides.
    """
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", "")
    write(repo / "facts" / "t.md", "\n---\ntopic: x\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")
    before = fact_rows(tmp_path, repo, "before")

    layout.migrate_legacy_facts(repo)

    merged = canonical.read_text(encoding="utf-8")
    units.parse_frontmatter(merged)  # must not raise
    assert merged.count("---") >= 2, "a delimiter the legacy file carried was deleted"
    assert "Legacy bullet arriving in the merge" in merged
    assert fact_rows(tmp_path, repo, "after") >= max(before, 1)


# The canonical side, which the first table never varied — and which is where the merge
# could bury facts with a perfectly well-formed legacy file, or none at all.
BROKEN_CANONICAL_HEADERS = {
    "unterminated": "---\ntopic: t\n",
    "terminated-but-malformed": "---\nnot a key line\n---\n",
    "bad-nested-block": "---\nowner:\n  not a nested key\n---\n",
}

LEGACY_SHAPES = {
    "well-formed-header": "---\ntopic: t\n---\n",
    "no-header-at-all": "",
}


@pytest.mark.parametrize("legacy_shape", sorted(LEGACY_SHAPES))
@pytest.mark.parametrize("canonical_shape", sorted(BROKEN_CANONICAL_HEADERS))
def test_facts_are_never_buried_in_a_canonical_file_that_does_not_parse(
    tmp_path, canonical_shape, legacy_shape
):
    """The merge is a convenience; keeping every fact readable is not.

    A canonical file whose own header the reader rejects yields nothing, so bullets folded
    into it stop being retrievable — with a well-formed legacy file, or one with no header
    at all, which is why no guard on the legacy side can catch this. The legacy file is
    kept beside it instead.
    """
    repo = make_repo(tmp_path)
    canonical = write(
        repo / CANON / "t.md", BROKEN_CANONICAL_HEADERS[canonical_shape] + CANONICAL_BULLET
    )
    canonical_bytes = canonical.read_bytes()
    write(repo / "facts" / "t.md", LEGACY_SHAPES[legacy_shape] + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")
    before = fact_rows(tmp_path, repo, "before")

    result = layout.migrate_legacy_facts(repo)

    assert fact_rows(tmp_path, repo, "after") >= before
    assert canonical.read_bytes() == canonical_bytes  # the broken file is left untouched
    aside = repo / CANON / "t.legacy.md"
    assert aside.is_file(), "the legacy file must be kept, not buried"
    assert "Legacy bullet arriving in the merge" in aside.read_text(encoding="utf-8")
    assert not (repo / "facts").exists()
    assert any("kept separate" in line for line in result.moved)


def test_a_colliding_frontmatter_key_never_takes_extra_lines_with_it(tmp_path):
    """`_meta_blocks` glues an unrecognised line to the PRECEDING key, so discarding a
    colliding key's block deleted those lines from a file the merge then removes."""
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", "---\ntags: x\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / "t.md", "---\ntags: x\n- a\n- b\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    layout.migrate_legacy_facts(repo)

    merged = canonical.read_text(encoding="utf-8")
    units.parse_frontmatter(merged)
    assert "- a" in merged and "- b" in merged  # glued lines survive somewhere
    assert "Legacy bullet arriving in the merge" in merged


def test_a_closing_code_fence_is_not_deleted_as_a_duplicate(tmp_path):
    """The dedup that dropped a `---` dropped a fence for the same reason; both are
    structure, and neither is the module's to delete."""
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", "---\ntopic: t\n---\n" + CANONICAL_BULLET)
    write(
        repo / "facts" / "t.md",
        "---\ntopic: t\n---\n" + LEGACY_BULLET + "```\ncode\n```\n",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    layout.migrate_legacy_facts(repo)

    merged = canonical.read_text(encoding="utf-8")
    assert merged.count("```") == 2, "the closing fence was deleted as a duplicate"


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


def test_the_report_says_when_a_header_was_demoted(tmp_path):
    """A migration that could not keep a header as metadata must say so, not report success.

    lint will flag nothing here — the merged file parses — but the reviewer of the pull
    request should know the legacy header became prose rather than keys.
    """
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", CANONICAL_BULLET)
    write(repo / "facts" / "t.md", "---\ntopic: t\nnot a key line\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    result = layout.migrate_legacy_facts(repo)

    notes = " ".join(result.merged)
    assert "header" in notes.lower()
    assert "body" in notes.lower()
